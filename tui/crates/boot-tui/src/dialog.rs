// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 模态对话框
//
// 会话层需要两种最基本的交互：确认一件事，或者输入一行文字。两者都不是
// 「弹一个框」那么简单——输入框要接受中文，光标必须落在字素簇边界上，
// 退格只能退一整个字素，不能把组合符或肤色修饰符留在原地。因此这里把
// 文本编辑做成对话框自己的状态机：插入、退格、删除、左右移动、行首行尾
// 都按字素簇推进，答复之后不再接受任何编辑。
//
// 对话框不自己画边框：版面由帧循环统一排，这里只给出「标题 + 正文 +
// 输入行（含光标）」的文本行。这样边框样式随主题走，对话框不必知道终端
// 宽度，也不会把转义序列混进状态里。
//
// SSoT: 0.0.5 方案 §14.4 字素簇口径、§14.9 执行面。
// ===========================================================================

//! 确认与单行输入对话框。

use unicode_segmentation::UnicodeSegmentation;

/// 光标标记；只占一列，不参与输入文本。
pub const CARET: char = '|';

/// 对话框的两种形态。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Kind {
    /// 二选一确认。
    Confirm,
    /// 单行文本输入。
    Input,
}

/// 用户对对话框的答复。
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Reply {
    /// 确认。
    Yes,
    /// 取消。
    No,
    /// 提交的文本。
    Text(String),
    /// 尚未答复。
    Open,
}

/// 对话框的内容与编辑状态。
pub struct Dialog {
    kind: Kind,
    title: String,
    body: Vec<String>,
    text: String,
    caret: usize,
    reply: Reply,
}

impl Dialog {
    /// 二选一确认。
    pub fn confirm(title: impl Into<String>, body: Vec<String>) -> Self {
        Self::new(Kind::Confirm, title.into(), body)
    }

    /// 单行文本输入。
    pub fn input(title: impl Into<String>, prompt: impl Into<String>) -> Self {
        Self::new(Kind::Input, title.into(), vec![prompt.into()])
    }

    /// 两种形态共用的构造。
    fn new(kind: Kind, title: String, body: Vec<String>) -> Self {
        Self {
            kind,
            title,
            body,
            text: String::new(),
            caret: 0,
            reply: Reply::Open,
        }
    }

    /// 形态。
    pub fn kind(&self) -> Kind {
        self.kind
    }

    /// 标题。
    pub fn title(&self) -> &str {
        &self.title
    }

    /// 正文各行。
    pub fn body(&self) -> &[String] {
        &self.body
    }

    /// 已输入的文字。
    pub fn text(&self) -> &str {
        &self.text
    }

    /// 光标所在字节偏移；总是字素簇边界。
    pub fn caret(&self) -> usize {
        self.caret
    }

    /// 当前答复。
    pub fn reply(&self) -> &Reply {
        &self.reply
    }

    /// 是否还在等待答复。
    pub fn pending(&self) -> bool {
        self.reply == Reply::Open
    }

    /// 在光标处插入一段文字；已答复或非输入框时忽略。
    pub fn insert(&mut self, text: &str) {
        if !self.editable() || text.is_empty() {
            return;
        }
        self.text.insert_str(self.caret, text);
        self.caret += text.len();
    }

    /// 删除光标前一个字素簇。
    pub fn backspace(&mut self) {
        if !self.editable() || self.caret == 0 {
            return;
        }
        let at = self.prev();
        self.text.replace_range(at..self.caret, "");
        self.caret = at;
    }

    /// 删除光标后一个字素簇。
    pub fn delete(&mut self) {
        if !self.editable() || self.caret == self.text.len() {
            return;
        }
        let at = self.next();
        self.text.replace_range(self.caret..at, "");
    }

    /// 光标左移一个字素簇。
    pub fn left(&mut self) {
        if self.editable() {
            self.caret = self.prev();
        }
    }

    /// 光标右移一个字素簇。
    pub fn right(&mut self) {
        if self.editable() {
            self.caret = self.next();
        }
    }

    /// 光标移到行首。
    pub fn home(&mut self) {
        if self.editable() {
            self.caret = 0;
        }
    }

    /// 光标移到行尾。
    pub fn end(&mut self) {
        if self.editable() {
            self.caret = self.text.len();
        }
    }

    /// 提交：确认框答「是」，输入框交回文本。
    pub fn enter(&mut self) {
        if !self.pending() {
            return;
        }
        self.reply = match self.kind {
            Kind::Confirm => Reply::Yes,
            Kind::Input => Reply::Text(self.text.clone()),
        };
    }

    /// 取消。
    pub fn cancel(&mut self) {
        if self.pending() {
            self.reply = Reply::No;
        }
    }

    /// 正文各行，供帧循环排进边框。
    pub fn rows(&self) -> Vec<String> {
        let mut rows = vec![self.title.clone(), String::new()];
        rows.extend(self.body.iter().cloned());
        if self.kind == Kind::Input {
            rows.push(String::new());
            rows.push(self.marked());
        }
        rows
    }

    /// 带光标标记的输入行。
    fn marked(&self) -> String {
        let mut line = String::with_capacity(self.text.len() + 1);
        line.push_str(&self.text[..self.caret]);
        line.push(CARET);
        line.push_str(&self.text[self.caret..]);
        line
    }

    /// 只有未答复的输入框接受编辑。
    fn editable(&self) -> bool {
        self.kind == Kind::Input && self.pending()
    }

    /// 光标前一个字素簇的起点。
    fn prev(&self) -> usize {
        self.text[..self.caret]
            .grapheme_indices(true)
            .next_back()
            .map_or(0, |(at, _)| at)
    }

    /// 光标后一个字素簇的终点。
    fn next(&self) -> usize {
        self.text[self.caret..]
            .graphemes(true)
            .next()
            .map_or(self.text.len(), |one| self.caret + one.len())
    }
}

#[cfg(test)]
mod tests {
    use super::{Dialog, Kind, Reply, CARET};

    fn input() -> Dialog {
        Dialog::input("输入指令", "> ")
    }

    fn typed(text: &str) -> Dialog {
        let mut dialog = input();
        dialog.insert(text);
        dialog
    }

    #[test]
    fn a_confirm_answers_yes_on_enter() {
        let mut dialog = Dialog::confirm("继续？", vec!["将重启服务。".to_owned()]);
        assert_eq!(dialog.kind(), Kind::Confirm);
        assert!(dialog.pending());
        dialog.enter();
        assert_eq!(*dialog.reply(), Reply::Yes);
        assert!(!dialog.pending());
    }

    #[test]
    fn a_confirm_answers_no_on_cancel() {
        let mut dialog = Dialog::confirm("继续？", Vec::new());
        dialog.cancel();
        assert_eq!(*dialog.reply(), Reply::No);
    }

    #[test]
    fn chinese_text_lands_at_the_caret() {
        let mut dialog = typed("极境操作");
        dialog.left();
        dialog.left();
        dialog.insert("系统");
        assert_eq!(dialog.text(), "极境系统操作");
        assert_eq!(&dialog.text()[..dialog.caret()], "极境系统");
    }

    #[test]
    fn backspace_removes_a_whole_cluster() {
        let mut dialog = typed("e\u{301}x");
        dialog.backspace();
        assert_eq!(dialog.text(), "e\u{301}");
        dialog.backspace();
        assert_eq!(dialog.text(), "");
    }

    #[test]
    fn delete_removes_the_cluster_after_the_caret() {
        let mut dialog = typed("e\u{301}x");
        dialog.home();
        dialog.delete();
        assert_eq!(dialog.text(), "x");
    }

    #[test]
    fn the_caret_stops_at_both_ends() {
        let mut dialog = typed("中文");
        for _ in 0..4 {
            dialog.left();
        }
        assert_eq!(dialog.caret(), 0);
        for _ in 0..4 {
            dialog.right();
        }
        assert_eq!(dialog.caret(), dialog.text().len());
    }

    #[test]
    fn home_and_end_jump_the_caret() {
        let mut dialog = typed("abc");
        dialog.home();
        assert_eq!(dialog.caret(), 0);
        dialog.end();
        assert_eq!(dialog.caret(), 3);
    }

    #[test]
    fn enter_returns_the_typed_text() {
        let mut dialog = typed("列出可用模型");
        dialog.enter();
        assert_eq!(*dialog.reply(), Reply::Text("列出可用模型".to_owned()));
    }

    #[test]
    fn an_answered_dialog_ignores_further_edits() {
        let mut dialog = typed("旧");
        dialog.enter();
        dialog.insert("新");
        dialog.backspace();
        assert_eq!(dialog.text(), "旧");
        assert_eq!(*dialog.reply(), Reply::Text("旧".to_owned()));
    }

    #[test]
    fn a_confirm_box_never_takes_text() {
        let mut dialog = Dialog::confirm("继续？", Vec::new());
        dialog.insert("不该出现");
        dialog.backspace();
        assert_eq!(dialog.text(), "");
    }

    #[test]
    fn rows_show_the_caret_between_clusters() {
        let mut dialog = input();
        dialog.insert("ab");
        dialog.left();
        let rows = dialog.rows();
        assert_eq!(rows.first().map(String::as_str), Some("输入指令"));
        assert_eq!(rows.last().map(String::as_str), Some("a|b"));
        assert!(rows.iter().any(|row| row == "> "));
        assert_eq!(rows.iter().filter(|row| row.contains(CARET)).count(), 1);
    }

    #[test]
    fn a_confirm_box_has_no_caret_row() {
        let dialog = Dialog::confirm("继续？", vec!["正文".to_owned()]);
        assert!(dialog.rows().iter().all(|row| !row.contains(CARET)));
    }
}
