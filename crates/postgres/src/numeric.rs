//! PostgreSQL `numeric` binary format ↔ decimal text, without precision loss (FR-008).
//!
//! Wire format: `ndigits: i16, weight: i16, sign: u16, dscale: u16`, then `ndigits` base-10000
//! digits (`i16`). `weight` is the power of 10000 of the first digit; `dscale` the number of
//! decimal digits after the point.

const POSITIVE: u16 = 0x0000;
const NEGATIVE: u16 = 0x4000;
const NAN: u16 = 0xC000;

/// Encodes plain decimal text (`-123.4500`, no exponent) into the binary format.
pub fn encode(text: &str) -> Result<Vec<u8>, String> {
    let invalid = || format!("'{text}' is not a decimal number");
    if text == "NaN" {
        return Ok(header(0, 0, NAN, 0));
    }
    let (negative, unsigned) = match text.as_bytes().first() {
        Some(b'-') => (true, &text[1..]),
        Some(b'+') => (false, &text[1..]),
        _ => (false, text),
    };
    let (integer, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
    if integer.is_empty() && fraction.is_empty()
        || !integer
            .bytes()
            .chain(fraction.bytes())
            .all(|b| b.is_ascii_digit())
    {
        return Err(invalid());
    }
    let dscale = u16::try_from(fraction.len()).map_err(|_| invalid())?;
    let integer = integer.trim_start_matches('0');

    let mut digits: Vec<i16> = Vec::new();
    let pad = (4 - integer.len() % 4) % 4;
    let padded: String = "0".repeat(pad) + integer;
    for chunk in padded.as_bytes().chunks(4) {
        digits.push(group(chunk));
    }
    let mut weight = digits.len() as i32 - 1;
    let padded_fraction = format!("{fraction}{}", "0".repeat((4 - fraction.len() % 4) % 4));
    for chunk in padded_fraction.as_bytes().chunks(4) {
        digits.push(group(chunk));
    }
    while digits.first() == Some(&0) {
        digits.remove(0);
        weight -= 1;
    }
    while digits.last() == Some(&0) {
        digits.pop();
    }
    if digits.is_empty() {
        return Ok(header(0, 0, POSITIVE, dscale));
    }
    let ndigits = i16::try_from(digits.len()).map_err(|_| invalid())?;
    let weight = i16::try_from(weight).map_err(|_| invalid())?;
    let mut out = header(
        ndigits,
        weight,
        if negative { NEGATIVE } else { POSITIVE },
        dscale,
    );
    for digit in digits {
        out.extend_from_slice(&digit.to_be_bytes());
    }
    Ok(out)
}

fn group(chunk: &[u8]) -> i16 {
    chunk
        .iter()
        .fold(0i16, |acc, b| acc * 10 + i16::from(b - b'0'))
}

fn header(ndigits: i16, weight: i16, sign: u16, dscale: u16) -> Vec<u8> {
    let mut out = Vec::with_capacity(8 + usize::try_from(ndigits).unwrap_or(0) * 2);
    out.extend_from_slice(&ndigits.to_be_bytes());
    out.extend_from_slice(&weight.to_be_bytes());
    out.extend_from_slice(&sign.to_be_bytes());
    out.extend_from_slice(&dscale.to_be_bytes());
    out
}

fn word(raw: &[u8], index: usize) -> Result<u16, String> {
    raw.get(index * 2..index * 2 + 2)
        .map(|b| u16::from_be_bytes([b[0], b[1]]))
        .ok_or_else(|| "truncated numeric value".to_string())
}

/// Decodes the binary format into plain decimal text with exactly `dscale` fraction digits.
pub fn decode(raw: &[u8]) -> Result<String, String> {
    let ndigits = word(raw, 0)? as i16 as i32;
    let weight = word(raw, 1)? as i16 as i32;
    let sign = word(raw, 2)?;
    let dscale = word(raw, 3)? as usize;
    if sign == NAN {
        return Ok("NaN".into());
    }
    let digits = (0..ndigits.max(0) as usize)
        .map(|i| word(raw, 4 + i).map(|w| w as i16 as i32))
        .collect::<Result<Vec<_>, _>>()?;
    let digit = |index: i32| -> i32 {
        if index < 0 {
            0
        } else {
            digits.get(index as usize).copied().unwrap_or(0)
        }
    };
    let mut out = String::new();
    if sign == NEGATIVE {
        out.push('-');
    }
    if weight < 0 {
        out.push('0');
    } else {
        for index in 0..=weight {
            if index == 0 {
                out.push_str(&digit(index).to_string());
            } else {
                out.push_str(&format!("{:04}", digit(index)));
            }
        }
    }
    if dscale > 0 {
        let groups = dscale.div_ceil(4) as i32;
        let mut fraction = String::with_capacity(groups as usize * 4);
        for k in 0..groups {
            fraction.push_str(&format!("{:04}", digit(weight + 1 + k)));
        }
        fraction.truncate(dscale);
        out.push('.');
        out.push_str(&fraction);
    }
    Ok(out)
}
