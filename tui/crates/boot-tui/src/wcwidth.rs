// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 列宽与截断的唯一入口
//
// 版面里每一个列宽都必须由本文件回答：宽字符两列、组合符零列、歧义宽度
// 固定一列。歧义宽度不跟随终端设置，否则同一份版面在不同终端上错位；
// 组合符零列保证「天 + 组合符」不被当成两列。
//
// 截断按字素簇推进：一旦下个字素簇放不进剩余列就整体停止，不允许把
// 「家庭 emoji」或带组合符的汉字切成两半——半截字素在终端里会占掉
// 两列，把后面整行推歪。
//
// 其他模块不得自行按 `chars().count()` 或字节长度推算列宽，统一走
// `display_width`；`ci/gates/width-unify.sh` 静态扫描这条约束。
//
// SSoT: 0.0.5 方案 §14.4。
// ===========================================================================

//! 列宽与截断的唯一入口。

use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

/// 一段文本占多少列；东亚宽度口径，歧义宽度固定一列。
pub fn display_width(text: &str) -> usize {
    UnicodeWidthStr::width(text)
}

/// 截到 `limit` 列以内，切口落在字素簇边界上。
pub fn clip_cols(text: &str, limit: u16) -> &str {
    let limit = usize::from(limit);
    let mut used = 0;
    for (at, grapheme) in text.grapheme_indices(true) {
        let span = display_width(grapheme);
        if used + span > limit {
            return &text[..at];
        }
        used += span;
    }
    text
}

#[cfg(test)]
mod tests {
    use super::{clip_cols, display_width};

    #[test]
    fn east_asian_wide_chars_take_two_columns() {
        assert_eq!(display_width("极"), 2);
        assert_eq!(display_width("极境OS"), 6);
    }

    #[test]
    fn narrow_and_ambiguous_chars_take_one_column() {
        assert_eq!(display_width("a"), 1);
        assert_eq!(display_width("·"), 1);
        assert_eq!(display_width("é"), 1);
    }

    #[test]
    fn a_combining_mark_adds_no_column() {
        assert_eq!(display_width("e\u{301}"), 1);
        assert_eq!(display_width("天\u{300}"), 2);
    }

    #[test]
    fn a_family_emoji_is_one_grapheme_but_two_columns() {
        let family = "\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}";
        assert_eq!(display_width(family), 2);
    }

    #[test]
    fn clipping_never_splits_a_grapheme() {
        let family = "\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}";
        assert_eq!(clip_cols(family, 1), "");
        assert_eq!(clip_cols(family, 2), family);
    }

    #[test]
    fn clipping_cuts_on_column_boundaries() {
        assert_eq!(clip_cols("极境OS", 1), "");
        assert_eq!(clip_cols("极境OS", 3), "极");
        assert_eq!(clip_cols("极境OS", 4), "极境");
        assert_eq!(clip_cols("abc", 9), "abc");
    }
}
