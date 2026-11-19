// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 候选生成与排序
//
// 同一串全拼对应多个词条时，候选顺序必须确定：同一输入查两次，用户看到
// 的候选列表要逐字节相同，否则「按数字选第几个」这类肌肉记忆会随机失
// 效。排序键因此固定为词频降序、文本字节序升序、拼音字节序升序，不引入
// 时间、随机数或哈希顺序。
//
// 同一个文本可能由不同拼音到达（多音字），对用户而言是重复候选；这里按
// 文本去重并保留排序最靠前的那一条（词频最高）。去重放在排序之后，保证
// 「保留哪一条」也由同一组键决定。
//
// SSoT: 0.0.5 方案 §14.6。
// ===========================================================================

//! 候选类型与确定性排序。

use core::cmp::Ordering;
use std::collections::HashSet;

use crate::dict::Entry;

/// 一条候选；文本与拼音都来自词典词条。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Candidate {
    /// 候选文本。
    pub text: String,
    /// 命中的全拼。
    pub pinyin: String,
    /// 词频。
    pub freq: u16,
}

impl Candidate {
    /// 从词条生成候选。
    pub fn of(entry: &Entry) -> Self {
        Self {
            text: entry.text().to_string(),
            pinyin: entry.pinyin().to_string(),
            freq: entry.freq(),
        }
    }
}

/// 候选的比较键：词频降序，同频按文本、再按拼音字节序升序。
pub fn order(left: &Candidate, right: &Candidate) -> Ordering {
    right
        .freq
        .cmp(&left.freq)
        .then_with(|| left.text.cmp(&right.text))
        .then_with(|| left.pinyin.cmp(&right.pinyin))
}

/// 排序并按文本去重；保留排序最靠前的一条。
///
/// 同一文本的两个拼音排完序后未必相邻（中间可能夹着同频的其他候选），
/// 故不能依赖 `Vec::dedup_by`，必须按「首次出现」全局去重。
pub fn rank(items: Vec<Candidate>) -> Vec<Candidate> {
    let mut items = items;
    items.sort_by(order);
    let mut seen = HashSet::new();
    items.retain(|item| seen.insert(item.text.clone()));
    items
}

#[cfg(test)]
mod tests {
    use super::*;

    fn candidate(text: &str, pinyin: &str, freq: u16) -> Candidate {
        Candidate {
            text: text.to_string(),
            pinyin: pinyin.to_string(),
            freq,
        }
    }

    #[test]
    fn higher_freq_comes_first() {
        let sorted = rank(vec![candidate("低", "di", 1), candidate("高", "gao", 9)]);
        assert_eq!(sorted[0].text, "高");
    }

    #[test]
    fn equal_freq_uses_text_bytes() {
        let sorted = rank(vec![
            candidate("钟", "zhong", 5),
            candidate("中", "zhong", 5),
        ]);
        assert_eq!(sorted[0].text, "中");
    }

    #[test]
    fn repeated_text_keeps_the_best_hit() {
        let sorted = rank(vec![
            candidate("重", "chong", 3),
            candidate("重", "zhong", 8),
            candidate("中", "zhong", 4),
        ]);
        assert_eq!(sorted.len(), 2);
        assert_eq!(sorted[0].text, "重");
        assert_eq!(sorted[0].pinyin, "zhong");
    }

    #[test]
    fn order_is_reproducible() {
        let items = vec![
            candidate("甲", "jia", 2),
            candidate("乙", "yi", 2),
            candidate("丙", "bing", 7),
        ];
        let first = rank(items.clone());
        let second = rank(items);
        assert_eq!(first, second);
    }
}
