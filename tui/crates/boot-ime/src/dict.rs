// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// `AIRYIME1` 词典的读写与校验
//
// 词典是引导期中文输入的唯一数据源，格式在本版首次定义（旧稿假设的
// 「既有词典」不成立）。权威说明见本 crate 的 SPEC.md；这里只负责把
// 那几段字节读成结构化词条，或把词条写回同样的字节，两向共用一套
// 常量，避免 SPEC 与实现各说一套。
//
// 失败一律 fail-closed：magic、版本、CRC 任一不符即拒绝整份词典，不
// 允许「跳过坏词条继续用」。坏词条留在内存里会让候选顺序在两次查询
// 之间变化，用户看到的是「同一个输入每次候选都不同」，比直接报
// `ime=no-dict` 更难排查。
//
// 校验只做契约要求的四件事：magic、ver、crc32、偏移不越界。顺序检查
// 额外做一步（拼音非降序），因为前缀检索用二分定位；顺序不成立时二分
// 会静默漏词，属于必须提前拒绝的结构错误。
//
// SSoT: 0.0.5 方案 §14.6；system/tui/crates/boot-ime/SPEC.md。
// ===========================================================================

//! `AIRYIME1` 词典的解析、校验与构建。

use core::fmt;

/// 词典魔数；文件前 8 字节必须逐字节相等。
pub const MAGIC: &[u8; 8] = b"AIRYIME1";

/// 词典版本；MAJOR 变更才改此值。
pub const VERSION: u16 = 1;

/// 头长度：魔数 8 + 版本 2 + 标志 2 + 词条数 4 + 拼音池长度 4 + CRC 4。
pub const HEADER_LEN: usize = 24;

/// 单条词条的表项长度：text_off 4 + py_off 4 + py_len 1 + text_len 1 + freq 2。
pub const ENTRY_LEN: usize = 12;

/// 单字段长度上限；`py_len` 与 `text_len` 在表项里各占 1 字节。
pub const FIELD_MAX: usize = 255;

/// 读入后的一个词条。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Entry {
    pinyin: String,
    text: String,
    freq: u16,
}

impl Entry {
    /// 由全拼、文本与词频构造；长度上限由 [`build`] 判定。
    pub fn new(pinyin: impl Into<String>, text: impl Into<String>, freq: u16) -> Self {
        Self {
            pinyin: pinyin.into(),
            text: text.into(),
            freq,
        }
    }

    /// 小写 ASCII 全拼，无分隔符。
    pub fn pinyin(&self) -> &str {
        &self.pinyin
    }

    /// UTF-8 候选文本，不含 NUL。
    pub fn text(&self) -> &str {
        &self.text
    }

    /// 词频；越大越靠前。
    pub fn freq(&self) -> u16 {
        self.freq
    }
}

/// 已加载的词典；词条保持文件里的顺序。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Dictionary {
    entries: Vec<Entry>,
}

impl Dictionary {
    /// 词条数。
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// 是否为空词典。
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// 按文件顺序读取词条；前缀检索见 [`crate::lookup::search`]。
    pub fn entries(&self) -> &[Entry] {
        &self.entries
    }

    /// 前缀检索的便捷入口；排序规则与 [`crate::lookup::search`] 一致。
    pub fn search(&self, prefix: &str, limit: usize) -> Vec<crate::rank::Candidate> {
        crate::lookup::search(&self.entries, prefix, limit)
    }
}

/// 词典读取失败的原因；任何一条都对应 `ime=no-dict`。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LoadError {
    /// 文件不足一个头。
    Short,
    /// 魔数不符。
    Magic,
    /// 版本不是 1。
    Version,
    /// CRC32 与头里记录的不符。
    Checksum,
    /// 词条表或池的偏移越界、字段为空。
    Bounds,
    /// 文本不是合法 UTF-8 或含 NUL。
    Text,
    /// 全拼不是小写 ASCII。
    Pinyin,
    /// 词条未按拼音非降序排列。
    Order,
}

impl fmt::Display for LoadError {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        let reason = match self {
            Self::Short => "词典不足一个头",
            Self::Magic => "词典魔数不符",
            Self::Version => "词典版本不支持",
            Self::Checksum => "词典 CRC32 不符",
            Self::Bounds => "词条偏移越界",
            Self::Text => "词条文本不合法",
            Self::Pinyin => "词条拼音不合法",
            Self::Order => "词条顺序不成立",
        };
        out.write_str(reason)
    }
}

/// 构建失败的原因；与读取失败分开，因为构建看到的是内存里的词条。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BuildError {
    /// 拼音或文本超过 255 字节。
    Long,
    /// 词条数超过 `u32` 能表示的范围。
    Many,
    /// 拼音不是小写 ASCII，或文本为空、含 NUL。
    Field,
}

impl fmt::Display for BuildError {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        let reason = match self {
            Self::Long => "拼音或文本超过 255 字节",
            Self::Many => "词条数超过 u32 上限",
            Self::Field => "拼音或文本不合法",
        };
        out.write_str(reason)
    }
}

/// 头里除 CRC 外的四个字段。
struct Header {
    entries: usize,
    pool: usize,
    checksum: u32,
}

impl Dictionary {
    /// 解析并校验整份词典；任何结构错误都整份拒绝。
    pub fn load(bytes: &[u8]) -> Result<Self, LoadError> {
        let header = read_header(bytes)?;
        let table = HEADER_LEN
            .checked_add(header.entries * ENTRY_LEN)
            .ok_or(LoadError::Bounds)?;
        let pool_end = table.checked_add(header.pool).ok_or(LoadError::Bounds)?;
        if pool_end > bytes.len() {
            return Err(LoadError::Bounds);
        }
        verify(bytes, header.checksum)?;

        let pools = bytes[table..pool_end].as_ref();
        let texts = &bytes[pool_end..];
        let mut entries = Vec::with_capacity(header.entries);
        for index in 0..header.entries {
            let raw = &bytes[HEADER_LEN + index * ENTRY_LEN..][..ENTRY_LEN];
            entries.push(read_entry(raw, pools, texts)?);
        }
        ensure_order(&entries)?;
        Ok(Self { entries })
    }
}

/// 读取并校验 24 字节头。
fn read_header(bytes: &[u8]) -> Result<Header, LoadError> {
    if bytes.len() < HEADER_LEN {
        return Err(LoadError::Short);
    }
    if &bytes[..8] != MAGIC {
        return Err(LoadError::Magic);
    }
    let version = u16::from_le_bytes([bytes[8], bytes[9]]);
    if version != VERSION {
        return Err(LoadError::Version);
    }
    let entries = u32::from_le_bytes([bytes[12], bytes[13], bytes[14], bytes[15]]) as usize;
    let pool = u32::from_le_bytes([bytes[16], bytes[17], bytes[18], bytes[19]]) as usize;
    let checksum = u32::from_le_bytes([bytes[20], bytes[21], bytes[22], bytes[23]]);
    Ok(Header {
        entries,
        pool,
        checksum,
    })
}

/// CRC32 覆盖头之后的全部字节。
fn verify(bytes: &[u8], expected: u32) -> Result<(), LoadError> {
    if crc32(&bytes[HEADER_LEN..]) == expected {
        Ok(())
    } else {
        Err(LoadError::Checksum)
    }
}

/// 解析一条 12 字节表项，并把它指向的两段字节解码成字符串。
fn read_entry(raw: &[u8], pools: &[u8], texts: &[u8]) -> Result<Entry, LoadError> {
    let text_off = word(raw, 0);
    let py_off = word(raw, 4);
    let py_len = usize::from(raw[8]);
    let text_len = usize::from(raw[9]);
    let freq = u16::from_le_bytes([raw[10], raw[11]]);

    let pinyin = slice(pools, py_off, py_len)?;
    let text = slice(texts, text_off, text_len)?;
    let pinyin = decode(pinyin, LoadError::Pinyin)?;
    let text = decode(text, LoadError::Text)?;
    if !pinyin.bytes().all(|byte| byte.is_ascii_lowercase()) {
        return Err(LoadError::Pinyin);
    }
    Ok(Entry::new(pinyin, text, freq))
}

/// 取表项里的一个 u32 小端字段。
fn word(raw: &[u8], at: usize) -> usize {
    let bytes = [raw[at], raw[at + 1], raw[at + 2], raw[at + 3]];
    u32::from_le_bytes(bytes) as usize
}

/// 按偏移与长度切一段；空段与越界同为 `Bounds`。
fn slice(pool: &[u8], offset: usize, len: usize) -> Result<&[u8], LoadError> {
    if len == 0 {
        return Err(LoadError::Bounds);
    }
    let end = offset.checked_add(len).ok_or(LoadError::Bounds)?;
    pool.get(offset..end).ok_or(LoadError::Bounds)
}

/// 把字节解码成字符串；NUL 与非法 UTF-8 一并拒绝。
fn decode(bytes: &[u8], error: LoadError) -> Result<String, LoadError> {
    if bytes.contains(&0) {
        return Err(error);
    }
    String::from_utf8(bytes.to_vec()).map_err(|_| error)
}

/// 前缀检索要求词条按拼音非降序；乱序会让二分静默漏词。
fn ensure_order(entries: &[Entry]) -> Result<(), LoadError> {
    let ordered = entries
        .windows(2)
        .all(|pair| pair[0].pinyin <= pair[1].pinyin);
    if ordered {
        Ok(())
    } else {
        Err(LoadError::Order)
    }
}

/// 按 SPEC 生成词典字节；同拼音同文本的重复词条保留最高词频。
pub fn build(source: &[Entry]) -> Result<Vec<u8>, BuildError> {
    let mut items = source.to_vec();
    items.sort_by(pair);
    items.dedup_by(|next, kept| {
        if next.pinyin == kept.pinyin && next.text == kept.text {
            kept.freq = kept.freq.max(next.freq);
            true
        } else {
            false
        }
    });
    let count = u32::try_from(items.len()).map_err(|_| BuildError::Many)?;
    let parts = pools(&items)?;
    assemble(count, &parts.table, &parts.pool, &parts.texts)
}

/// 词条在文件里的顺序：先拼音字节序，再文本字节序。
pub(crate) fn pair(left: &Entry, right: &Entry) -> core::cmp::Ordering {
    left.pinyin
        .cmp(&right.pinyin)
        .then_with(|| left.text.cmp(&right.text))
}

/// 词条拆出的三块字节：表项区、拼音池与文本池。
struct Pools {
    table: Vec<u8>,
    pool: Vec<u8>,
    texts: Vec<u8>,
}

/// 把词条拆成表项、拼音池与文本池。
fn pools(items: &[Entry]) -> Result<Pools, BuildError> {
    let mut table = Vec::with_capacity(items.len() * ENTRY_LEN);
    let mut pool = Vec::new();
    let mut texts = Vec::new();
    for entry in items {
        let py = entry.pinyin.as_bytes();
        let text = entry.text.as_bytes();
        check(py, text)?;
        let py_off = u32::try_from(pool.len()).map_err(|_| BuildError::Many)?;
        let text_off = u32::try_from(texts.len()).map_err(|_| BuildError::Many)?;
        table.extend_from_slice(&text_off.to_le_bytes());
        table.extend_from_slice(&py_off.to_le_bytes());
        table.push(py.len() as u8);
        table.push(text.len() as u8);
        table.extend_from_slice(&entry.freq.to_le_bytes());
        pool.extend_from_slice(py);
        texts.extend_from_slice(text);
    }
    Ok(Pools { table, pool, texts })
}

/// 构建前的字段校验；空字段与超长字段分别报错。
fn check(py: &[u8], text: &[u8]) -> Result<(), BuildError> {
    if py.is_empty() || text.is_empty() {
        return Err(BuildError::Field);
    }
    if py.len() > FIELD_MAX || text.len() > FIELD_MAX {
        return Err(BuildError::Long);
    }
    if !py.iter().all(u8::is_ascii_lowercase) || text.contains(&0) {
        return Err(BuildError::Field);
    }
    Ok(())
}

/// 拼出完整文件：头、表项、拼音池、文本池；CRC 覆盖头之后全部字节。
fn assemble(count: u32, table: &[u8], pool: &[u8], texts: &[u8]) -> Result<Vec<u8>, BuildError> {
    let pool_len = u32::try_from(pool.len()).map_err(|_| BuildError::Many)?;
    let mut out = Vec::with_capacity(HEADER_LEN + table.len() + pool.len() + texts.len());
    out.resize(HEADER_LEN, 0);
    out.extend_from_slice(table);
    out.extend_from_slice(pool);
    out.extend_from_slice(texts);
    out[..8].copy_from_slice(MAGIC);
    out[8..10].copy_from_slice(&VERSION.to_le_bytes());
    out[12..16].copy_from_slice(&count.to_le_bytes());
    out[16..20].copy_from_slice(&pool_len.to_le_bytes());
    let checksum = crc32(&out[HEADER_LEN..]);
    out[20..24].copy_from_slice(&checksum.to_le_bytes());
    Ok(out)
}

/// CRC32（IEEE 802.3，反射多项式 0xEDB88320）；无外部依赖。
pub fn crc32(bytes: &[u8]) -> u32 {
    let mut crc = !0u32;
    for byte in bytes {
        crc ^= u32::from(*byte);
        for _ in 0..8 {
            let mask = (crc & 1).wrapping_neg();
            crc = (crc >> 1) ^ (0xEDB8_8320 & mask);
        }
    }
    !crc
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Vec<Entry> {
        vec![
            Entry::new("zhong", "中", 900),
            Entry::new("zhong", "钟", 400),
            Entry::new("zhongguo", "中国", 950),
            Entry::new("ni", "你", 800),
        ]
    }

    fn loaded() -> Vec<Entry> {
        let bytes = build(&sample()).expect("示例可构建");
        Dictionary::load(&bytes)
            .expect("自建词典必须可加载")
            .entries()
            .to_vec()
    }

    #[test]
    fn roundtrip_preserves_the_table() {
        let entries = loaded();
        assert_eq!(entries.len(), 4);
        assert_eq!(entries[0].text(), "你");
        assert_eq!(entries[3].text(), "中国");
    }

    #[test]
    fn build_is_deterministic() {
        let first = build(&sample()).expect("可构建");
        let second = build(&sample()).expect("可构建");
        assert_eq!(first, second);
    }

    #[test]
    fn duplicate_entry_keeps_highest_freq() {
        let mut items = sample();
        items.push(Entry::new("ni", "你", 700));
        let entries = Dictionary::load(&build(&items).expect("可构建"))
            .expect("可加载")
            .entries()
            .to_vec();
        assert_eq!(entries.iter().filter(|e| e.text() == "你").count(), 1);
        assert_eq!(
            entries.iter().find(|e| e.text() == "你").map(Entry::freq),
            Some(800)
        );
    }

    #[test]
    fn bad_magic_is_rejected() {
        let mut bytes = build(&sample()).expect("可构建");
        bytes[0] = b'X';
        assert_eq!(Dictionary::load(&bytes), Err(LoadError::Magic));
    }

    #[test]
    fn bad_version_is_rejected() {
        let mut bytes = build(&sample()).expect("可构建");
        bytes[8] = 2;
        assert_eq!(Dictionary::load(&bytes), Err(LoadError::Version));
    }

    #[test]
    fn bad_checksum_is_rejected() {
        let mut bytes = build(&sample()).expect("可构建");
        let last = bytes.len() - 1;
        bytes[last] ^= 0xff;
        assert_eq!(Dictionary::load(&bytes), Err(LoadError::Checksum));
    }

    #[test]
    fn short_header_is_rejected() {
        assert_eq!(Dictionary::load(&[0u8; 4]), Err(LoadError::Short));
    }

    #[test]
    fn uppercase_pinyin_is_rejected() {
        assert_eq!(
            build(&[Entry::new("Zhong", "中", 1)]),
            Err(BuildError::Field)
        );
    }

    #[test]
    fn long_field_is_rejected() {
        let long = "a".repeat(FIELD_MAX + 1);
        assert_eq!(build(&[Entry::new(long, "中", 1)]), Err(BuildError::Long));
    }
}
