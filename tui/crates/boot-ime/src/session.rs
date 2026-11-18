// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 输入法会话状态机
//
// 会话只做三件事：把按键累积成全拼串、按前缀取候选、把选中的候选提交成
// 文本。界面怎么画候选、退出键怎么映射都不在这里。
//
// 重放由 seq 决定：每个会话内 seq 严格递增，进程重启后 boot-tui 会按原
// seq 重发未提交的按键。收到 `seq <= 已处理` 的帧一律当作重放丢弃，否则
// 一次崩溃恢复会把同一个字母打两遍——这正是「按 seq 对齐」的全部意义。
// 已提交或已重置的动作会清空重放窗口，窗口最多保留 REPLAY 条。
//
// 提交语义：有候选就提交选中项，没有候选就提交原始全拼串（用户至少能
// 拿回自己敲的字母，而不是丢失输入）；两者都为空则视为空操作。
//
// SSoT: 0.0.5 方案 §14.6；本 crate 的 SPEC.md。
// ===========================================================================

//! 会话状态机：按键 → 候选 → 提交。

use core::fmt;
use std::collections::VecDeque;

use crate::dict::Dictionary;
use crate::ipc::{Frame, Msg, REPLAY};
use crate::lookup::search;
use crate::rank::Candidate;

/// 单次组合最多展示的候选数，与数字键 1–9 对齐。
pub const LIMIT: usize = 9;

/// 一次输入给界面的结果。
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Effect {
    /// 重放、空提交或状态未变；界面无需重绘。
    Idle,
    /// 组合串更新；`keys` 为空表示组合已清空。
    Composing {
        /// 当前全拼串。
        keys: String,
        /// 当前候选，最多 [`LIMIT`] 条。
        candidates: Vec<Candidate>,
        /// 选中项下标。
        picked: usize,
    },
    /// 已提交的文本；组合随之清空。
    Commit(String),
    /// 会话已关闭，后续帧一律拒绝。
    Closed,
}

/// 会话错误；全部由调用方的帧触发，不做静默纠正。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SessError {
    /// 会话标识为空。
    Empty,
    /// 会话已关闭。
    Closed,
    /// 帧的会话标识与当前会话不符。
    Foreign,
    /// 消息缺少该类别必需的字段。
    Shape,
    /// 按键不是单个小写或大写 ASCII 字母。
    Key,
    /// 候选下标越界。
    Index,
}

impl fmt::Display for SessError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let text = match self {
            Self::Empty => "会话标识为空",
            Self::Closed => "会话已关闭",
            Self::Foreign => "会话标识不匹配",
            Self::Shape => "消息字段与类别不符",
            Self::Key => "按键不是单个 ASCII 字母",
            Self::Index => "候选下标越界",
        };
        f.write_str(text)
    }
}

/// 一个输入会话；词典只读借用，状态全部私有。
pub struct Session<'d> {
    dict: &'d Dictionary,
    id: String,
    last: u64,
    pending: VecDeque<Frame>,
    keys: String,
    cands: Vec<Candidate>,
    picked: usize,
    open: bool,
}

impl<'d> Session<'d> {
    /// 建立会话；空标识直接拒绝。
    pub fn open(dict: &'d Dictionary, id: impl Into<String>) -> Result<Self, SessError> {
        let id = id.into();
        if id.is_empty() {
            return Err(SessError::Empty);
        }
        Ok(Self {
            dict,
            id,
            last: 0,
            pending: VecDeque::new(),
            keys: String::new(),
            cands: Vec::new(),
            picked: 0,
            open: true,
        })
    }

    /// 会话标识。
    pub fn id(&self) -> &str {
        &self.id
    }

    /// 是否仍可接收帧。
    pub fn is_open(&self) -> bool {
        self.open
    }

    /// 当前全拼串。
    pub fn composing(&self) -> &str {
        &self.keys
    }

    /// 当前候选。
    pub fn candidates(&self) -> &[Candidate] {
        &self.cands
    }

    /// 当前选中项下标。
    pub fn picked(&self) -> usize {
        self.picked
    }

    /// 重放窗口里的未提交帧，按 seq 升序。
    pub fn pending(&self) -> impl Iterator<Item = &Frame> {
        self.pending.iter()
    }

    /// 应用一帧；重放帧与空操作返回 [`Effect::Idle`]。
    pub fn apply(&mut self, frame: &Frame) -> Result<Effect, SessError> {
        self.guard(frame)?;
        if frame.seq <= self.last {
            return Ok(Effect::Idle);
        }
        let effect = self.step(frame)?;
        self.last = frame.seq;
        Ok(effect)
    }

    /// 前置条件：会话未关闭且帧属于本会话。
    fn guard(&self, frame: &Frame) -> Result<(), SessError> {
        if !self.open {
            return Err(SessError::Closed);
        }
        if frame.session != self.id {
            return Err(SessError::Foreign);
        }
        Ok(())
    }

    /// 按消息类别推进状态。
    fn step(&mut self, frame: &Frame) -> Result<Effect, SessError> {
        match frame.msg {
            Msg::Open => Ok(Effect::Idle),
            Msg::Key => self.press(frame),
            Msg::Select => self.pick(frame),
            Msg::Commit => Ok(self.commit()),
            Msg::Reset => Ok(self.reset()),
            Msg::Close => {
                self.open = false;
                self.pending.clear();
                Ok(Effect::Closed)
            }
        }
    }

    /// 一键一字母；大写折成小写，其余一律拒绝。
    fn press(&mut self, frame: &Frame) -> Result<Effect, SessError> {
        let raw = frame.key.as_deref().ok_or(SessError::Shape)?;
        let mut chars = raw.chars();
        let (Some(first), None) = (chars.next(), chars.next()) else {
            return Err(SessError::Shape);
        };
        let lower = first.to_ascii_lowercase();
        if !lower.is_ascii_lowercase() {
            return Err(SessError::Key);
        }
        self.keys.push(lower);
        self.cands = search(self.dict.entries(), &self.keys, LIMIT);
        self.picked = 0;
        self.remember(frame);
        Ok(self.composed())
    }

    /// 选中第几号候选；越界拒绝而不夹取。
    fn pick(&mut self, frame: &Frame) -> Result<Effect, SessError> {
        let index = frame.index.ok_or(SessError::Shape)? as usize;
        if index >= self.cands.len() {
            return Err(SessError::Index);
        }
        self.picked = index;
        self.remember(frame);
        Ok(self.composed())
    }

    /// 提交选中候选；无候选时提交原始全拼。
    fn commit(&mut self) -> Effect {
        let text = match self.cands.get(self.picked) {
            Some(candidate) => candidate.text.clone(),
            None => self.keys.clone(),
        };
        if text.is_empty() {
            return Effect::Idle;
        }
        self.clear();
        Effect::Commit(text)
    }

    /// 清空组合；重放窗口随之关闭，已发生的动作无需再放。
    fn reset(&mut self) -> Effect {
        self.clear();
        self.composed()
    }

    /// 记入重放窗口；超过 [`REPLAY`] 条时丢弃最旧的一条。
    fn remember(&mut self, frame: &Frame) {
        if self.pending.len() == REPLAY {
            self.pending.pop_front();
        }
        self.pending.push_back(frame.clone());
    }

    /// 组合与重放一起清空。
    fn clear(&mut self) {
        self.keys.clear();
        self.cands.clear();
        self.picked = 0;
        self.pending.clear();
    }

    /// 把当前状态包装成组合效果。
    fn composed(&self) -> Effect {
        Effect::Composing {
            keys: self.keys.clone(),
            candidates: self.cands.clone(),
            picked: self.picked,
        }
    }
}

impl fmt::Debug for Session<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Session")
            .field("id", &self.id)
            .field("last", &self.last)
            .field("keys", &self.keys)
            .field("candidates", &self.cands.len())
            .field("picked", &self.picked)
            .field("open", &self.open)
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dict::{build, Entry};

    fn dictionary() -> Dictionary {
        let entries = vec![
            Entry::new("ni", "你", 800),
            Entry::new("zhong", "中", 900),
            Entry::new("zhong", "钟", 400),
            Entry::new("zhongguo", "中国", 950),
        ];
        Dictionary::load(&build(&entries).expect("可构建")).expect("可加载")
    }

    fn session() -> Session<'static> {
        // 词典是 'static 的测试常量，借用不逃出本模块。
        let dict = Box::leak(Box::new(dictionary()));
        Session::open(dict, "s-1").expect("可开会话")
    }

    /// 逐字母发送；返回下一个可用 seq。
    fn type_keys(sess: &mut Session<'_>, first: u64, text: &str) -> u64 {
        let mut seq = first;
        for ch in text.chars() {
            let key = ch.to_string();
            sess.apply(&Frame::key("s-1", seq, key)).expect("可按键");
            seq += 1;
        }
        seq
    }

    #[test]
    fn typing_builds_candidates() {
        let mut sess = session();
        type_keys(&mut sess, 1, "z");
        let effect = sess.apply(&Frame::key("s-1", 2, "h")).expect("可按键");
        type_keys(&mut sess, 3, "ong");
        assert_eq!(sess.composing(), "zhong");
        match effect {
            Effect::Composing {
                keys,
                candidates,
                picked,
            } => {
                assert_eq!(keys, "zh");
                assert!(!candidates.is_empty());
                assert_eq!(picked, 0);
            }
            other => panic!("期望组合效果，得到 {other:?}"),
        }
    }

    #[test]
    fn uppercase_is_folded() {
        let mut sess = session();
        sess.apply(&Frame::key("s-1", 1, "Z")).expect("可按键");
        assert_eq!(sess.composing(), "z");
    }

    #[test]
    fn replay_is_idempotent() {
        let mut sess = session();
        let frame = Frame::key("s-1", 1, "n");
        sess.apply(&frame).expect("首放");
        assert_eq!(sess.apply(&frame).expect("重放"), Effect::Idle);
        assert_eq!(sess.composing(), "n");
    }

    #[test]
    fn commit_uses_the_picked_candidate() {
        let mut sess = session();
        let mut seq = type_keys(&mut sess, 1, "zhong");
        let expected = sess.candidates()[1].text.clone();
        sess.apply(&Frame::select("s-1", seq, 1)).expect("选择");
        seq += 1;
        let effect = sess
            .apply(&Frame::new("s-1", seq, Msg::Commit))
            .expect("提交");
        let Effect::Commit(text) = effect else {
            panic!("期望提交，得到 {effect:?}");
        };
        assert_eq!(text, expected);
        assert_eq!(sess.composing(), "");
        assert_eq!(sess.pending().count(), 0);
    }

    #[test]
    fn commit_without_candidates_keeps_raw_keys() {
        let mut sess = session();
        let seq = type_keys(&mut sess, 1, "xyz");
        let effect = sess
            .apply(&Frame::new("s-1", seq, Msg::Commit))
            .expect("提交");
        assert_eq!(effect, Effect::Commit("xyz".to_string()));
    }

    #[test]
    fn empty_commit_is_idle() {
        let mut sess = session();
        assert_eq!(
            sess.apply(&Frame::new("s-1", 1, Msg::Commit))
                .expect("提交"),
            Effect::Idle
        );
    }

    #[test]
    fn select_out_of_range_is_rejected() {
        let mut sess = session();
        let seq = type_keys(&mut sess, 1, "zhong");
        let bad = Frame::select("s-1", seq, 200);
        assert_eq!(sess.apply(&bad), Err(SessError::Index));
    }

    #[test]
    fn malformed_key_is_rejected() {
        let mut sess = session();
        for shape in ["", "ab"] {
            let frame = Frame::key("s-1", 1, shape);
            assert_eq!(sess.apply(&frame), Err(SessError::Shape));
        }
        for key in ["1", "，"] {
            let frame = Frame::key("s-1", 1, key);
            assert_eq!(sess.apply(&frame), Err(SessError::Key));
        }
    }

    #[test]
    fn foreign_session_is_rejected() {
        let mut sess = session();
        let frame = Frame::key("s-2", 1, "n");
        assert_eq!(sess.apply(&frame), Err(SessError::Foreign));
    }

    #[test]
    fn closed_session_refuses_frames() {
        let mut sess = session();
        let effect = sess.apply(&Frame::new("s-1", 1, Msg::Close)).expect("关闭");
        assert_eq!(effect, Effect::Closed);
        assert!(!sess.is_open());
        let frame = Frame::key("s-1", 2, "n");
        assert_eq!(sess.apply(&frame), Err(SessError::Closed));
    }

    #[test]
    fn pending_window_is_bounded() {
        let mut sess = session();
        for seq in 1..=(REPLAY as u64 + 10) {
            sess.apply(&Frame::key("s-1", seq, "n")).expect("按键");
        }
        assert_eq!(sess.pending().count(), REPLAY);
    }

    #[test]
    fn reset_clears_composition_and_replay() {
        let mut sess = session();
        let seq = type_keys(&mut sess, 1, "zhong");
        let effect = sess
            .apply(&Frame::new("s-1", seq, Msg::Reset))
            .expect("重置");
        assert_eq!(
            effect,
            Effect::Composing {
                keys: String::new(),
                candidates: Vec::new(),
                picked: 0,
            }
        );
        assert_eq!(sess.pending().count(), 0);
    }

    #[test]
    fn empty_session_id_is_rejected() {
        let dict = Box::leak(Box::new(dictionary()));
        assert_eq!(Session::open(dict, "").unwrap_err(), SessError::Empty);
    }
}
