// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 前缀检索
//
// 词典按拼音非降序排列，所以前缀检索先用二分定位第一个「不小于前缀」
// 的词条，再向后线性扫描到第一个不再以前缀开头的词条。二分只依赖比较
// 函数与文件里的顺序一致，因此加载阶段已经把乱序词典整份拒绝。
//
// 前缀只接受小写 ASCII 全拼：空串会命中整部词典，非 ASCII 输入不是全拼
// 键序，两者都直接返回空候选，而不是在检索层做猜测式的规范化——规范化
// 属于键序列解码（N-02），到这里之前就必须完成。
//
// 候选顺序交给 [`crate::rank`] 统一决定，检索不自己排一遍。
//
// SSoT: 0.0.5 方案 §14.6。
// ===========================================================================

//! 前缀二分检索与候选收集。

use crate::dict::Entry;
use crate::rank::{rank, Candidate};

/// 按前缀检索候选，最多返回 `limit` 条；空前缀与非法前缀返回空。
pub fn search(entries: &[Entry], prefix: &str, limit: usize) -> Vec<Candidate> {
    if limit == 0 || !usable(prefix) {
        return Vec::new();
    }
    let start = entries.partition_point(|entry| entry.pinyin() < prefix);
    let hits: Vec<Candidate> = entries[start..]
        .iter()
        .take_while(|entry| entry.pinyin().starts_with(prefix))
        .map(Candidate::of)
        .collect();
    let mut ranked = rank(hits);
    ranked.truncate(limit);
    ranked
}

/// 前缀是否可能命中：非空且全部是小写 ASCII。
fn usable(prefix: &str) -> bool {
    !prefix.is_empty() && prefix.bytes().all(|byte| byte.is_ascii_lowercase())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dict::{build, Dictionary, Entry};

    fn dictionary() -> Dictionary {
        let entries = vec![
            Entry::new("ni", "你", 800),
            Entry::new("zhong", "中", 900),
            Entry::new("zhong", "钟", 400),
            Entry::new("zhongguo", "中国", 950),
            Entry::new("zi", "字", 100),
        ];
        let bytes = build(&entries).expect("示例可构建");
        Dictionary::load(&bytes).expect("示例可加载")
    }

    #[test]
    fn exact_prefix_finds_all_homophones() {
        let words = dictionary();
        let hits = search(words.entries(), "zhong", 9);
        assert_eq!(hits.len(), 3);
        assert_eq!(hits[0].text, "中国");
        assert_eq!(hits[1].text, "中");
        assert_eq!(hits[2].text, "钟");
    }

    #[test]
    fn prefix_stops_before_the_next_group() {
        let words = dictionary();
        let hits = search(words.entries(), "zho", 9);
        assert_eq!(hits.len(), 3);
        assert!(hits.iter().all(|hit| hit.pinyin.starts_with("zho")));
    }

    #[test]
    fn missing_prefix_returns_nothing() {
        let words = dictionary();
        assert!(search(words.entries(), "zhx", 9).is_empty());
    }

    #[test]
    fn empty_or_non_ascii_prefix_is_ignored() {
        let words = dictionary();
        assert!(search(words.entries(), "", 9).is_empty());
        assert!(search(words.entries(), "中", 9).is_empty());
    }

    #[test]
    fn limit_caps_the_candidates() {
        let words = dictionary();
        assert_eq!(search(words.entries(), "zhong", 2).len(), 2);
        assert!(search(words.entries(), "zhong", 0).is_empty());
    }
}
