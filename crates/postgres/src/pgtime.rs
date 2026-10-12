//! Date/time text (as produced by the bridge) ↔ PostgreSQL binary values, whose epoch is
//! 2000-01-01. Uses Howard Hinnant's civil-date algorithms (proleptic Gregorian calendar).

const PG_EPOCH_DAYS: i64 = 10_957; // 2000-01-01 counted from 1970-01-01
const MICROS_PER_DAY: i64 = 86_400_000_000;

fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = if year >= 0 { year } else { year - 399 } / 400;
    let year_of_era = year - era * 400;
    let month_index = (month + 9) % 12;
    let day_of_year = (153 * month_index + 2) / 5 + day - 1;
    let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    era * 146_097 + day_of_era - 719_468
}

fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let day_of_era = z - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_index = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_index + 2) / 5 + 1;
    let month = if month_index < 10 {
        month_index + 3
    } else {
        month_index - 9
    };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

fn number(text: &str, what: &str) -> Result<i64, String> {
    if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
        return Err(format!("invalid {what} '{text}'"));
    }
    text.parse().map_err(|_| format!("invalid {what} '{text}'"))
}

fn days_in_month(year: i64, month: i64) -> i64 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 => 29,
        _ => 28,
    }
}

/// `YYYY-MM-DD` → days since 2000-01-01.
pub fn date_to_days(text: &str) -> Result<i32, String> {
    let mut parts = text.splitn(3, '-');
    let (Some(y), Some(m), Some(d)) = (parts.next(), parts.next(), parts.next()) else {
        return Err(format!("invalid date '{text}'"));
    };
    let (year, month, day) = (number(y, "year")?, number(m, "month")?, number(d, "day")?);
    if !(1..=12).contains(&month) || day < 1 || day > days_in_month(year, month) {
        return Err(format!("invalid date '{text}'"));
    }
    i32::try_from(days_from_civil(year, month, day) - PG_EPOCH_DAYS)
        .map_err(|_| format!("date out of range '{text}'"))
}

pub fn days_to_date(days: i32) -> String {
    let (year, month, day) = civil_from_days(i64::from(days) + PG_EPOCH_DAYS);
    format!("{year:04}-{month:02}-{day:02}")
}

/// `HH:MM:SS[.ffffff]` → microseconds since midnight.
pub fn time_to_micros(text: &str) -> Result<i64, String> {
    let invalid = || format!("invalid time '{text}'");
    let (clock, fraction) = text.split_once('.').unwrap_or((text, ""));
    let mut parts = clock.splitn(3, ':');
    let (Some(h), Some(m), Some(s)) = (parts.next(), parts.next(), parts.next()) else {
        return Err(invalid());
    };
    let (hour, minute, second) = (
        number(h, "hour")?,
        number(m, "minute")?,
        number(s, "second")?,
    );
    if hour > 23 || minute > 59 || second > 59 || fraction.len() > 6 {
        return Err(invalid());
    }
    let micros = if fraction.is_empty() {
        0
    } else {
        number(fraction, "fraction")? * 10i64.pow(6 - fraction.len() as u32)
    };
    Ok(((hour * 60 + minute) * 60 + second) * 1_000_000 + micros)
}

pub fn micros_to_time(micros: i64) -> String {
    let fraction = micros % 1_000_000;
    let seconds = micros / 1_000_000;
    format!(
        "{:02}:{:02}:{:02}.{fraction:06}",
        seconds / 3600,
        seconds / 60 % 60,
        seconds % 60
    )
}

/// `YYYY-MM-DD HH:MM:SS[.ffffff]` (UTC) → microseconds since 2000-01-01 00:00 UTC.
pub fn timestamp_to_micros(text: &str) -> Result<i64, String> {
    let (date, time) = text
        .split_once([' ', 'T'])
        .ok_or_else(|| format!("invalid timestamp '{text}'"))?;
    Ok(i64::from(date_to_days(date)?) * MICROS_PER_DAY + time_to_micros(time)?)
}

pub fn micros_to_timestamp(micros: i64) -> String {
    let days = micros.div_euclid(MICROS_PER_DAY);
    let rest = micros.rem_euclid(MICROS_PER_DAY);
    format!(
        "{} {}",
        days_to_date(i32::try_from(days).unwrap_or(i32::MAX)),
        micros_to_time(rest)
    )
}
