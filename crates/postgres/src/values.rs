//! Parameter encoding and result decoding by PostgreSQL type (research R3). Values keep the
//! database-independent shapes the ORM uses; conversion to native types happens here.

use std::error::Error;

use bytes::{BufMut, BytesMut};
use ferro_database::Value;
use tokio_postgres::Row;
use tokio_postgres::types::{FromSql, IsNull, ToSql, Type, to_sql_checked};

use crate::{numeric, pgtime};

type BoxError = Box<dyn Error + Sync + Send>;

fn mismatch(value: &Value, ty: &Type) -> BoxError {
    let kind = match value {
        Value::Null => "NULL",
        Value::Integer(_) => "integer",
        Value::Real(_) => "float",
        Value::Decimal(_) => "decimal",
        Value::Text(_) => "text",
        Value::Blob(_) => "bytes",
    };
    format!("cannot send a {kind} value as {}", ty.name()).into()
}

fn uuid_bytes(text: &str) -> Result<[u8; 16], BoxError> {
    let hex: String = text.chars().filter(|c| *c != '-').collect();
    if hex.len() != 32 {
        return Err(format!("invalid UUID '{text}'").into());
    }
    let mut out = [0u8; 16];
    for (i, byte) in out.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16)
            .map_err(|_| format!("invalid UUID '{text}'"))?;
    }
    Ok(out)
}

#[derive(Debug)]
pub(crate) struct Param<'a>(pub(crate) &'a Value);

impl ToSql for Param<'_> {
    fn to_sql(&self, ty: &Type, out: &mut BytesMut) -> Result<IsNull, BoxError> {
        let value = self.0;
        match (value, ty) {
            (Value::Null, _) => return Ok(IsNull::Yes),
            (Value::Integer(v), &Type::BOOL) => out.put_u8(u8::from(*v != 0)),
            (Value::Integer(v), &Type::INT2) => out.put_i16(i16::try_from(*v)?),
            (Value::Integer(v), &Type::INT4) => out.put_i32(i32::try_from(*v)?),
            (Value::Integer(v), &Type::INT8) => out.put_i64(*v),
            (Value::Integer(v), &Type::FLOAT8) => out.put_f64(*v as f64),
            (Value::Integer(v), &Type::FLOAT4) => out.put_f32(*v as f32),
            (Value::Integer(v), &Type::NUMERIC) => out.put_slice(&numeric::encode(&v.to_string())?),
            (Value::Real(v), &Type::FLOAT8) => out.put_f64(*v),
            (Value::Real(v), &Type::FLOAT4) => out.put_f32(*v as f32),
            (Value::Real(v), &Type::NUMERIC) => out.put_slice(&numeric::encode(&v.to_string())?),
            (Value::Decimal(v), &Type::NUMERIC) => out.put_slice(&numeric::encode(v)?),
            (Value::Decimal(v), &Type::FLOAT8) => out.put_f64(v.parse()?),
            (Value::Decimal(v), &Type::INT8) => out.put_i64(v.parse()?),
            (Value::Decimal(v), &Type::INT4) => out.put_i32(v.parse()?),
            (Value::Text(v), &Type::UUID) => out.put_slice(&uuid_bytes(v)?),
            (Value::Text(v), &Type::DATE) => out.put_i32(pgtime::date_to_days(v)?),
            (Value::Text(v), &Type::TIMESTAMPTZ | &Type::TIMESTAMP) => {
                out.put_i64(pgtime::timestamp_to_micros(v)?)
            }
            (Value::Text(v), &Type::TIME) => out.put_i64(pgtime::time_to_micros(v)?),
            (Value::Text(v), &Type::JSONB) => {
                out.put_u8(1);
                out.put_slice(v.as_bytes());
            }
            (Value::Text(v), &Type::NUMERIC) => out.put_slice(&numeric::encode(v)?),
            (Value::Text(v), _) if is_text(ty) || *ty == Type::JSON => out.put_slice(v.as_bytes()),
            (Value::Blob(v), &Type::BYTEA) => out.put_slice(v),
            (value, ty) => return Err(mismatch(value, ty)),
        }
        Ok(IsNull::No)
    }

    fn accepts(_ty: &Type) -> bool {
        true
    }

    to_sql_checked!();
}

fn is_text(ty: &Type) -> bool {
    matches!(
        *ty,
        Type::TEXT | Type::VARCHAR | Type::BPCHAR | Type::NAME | Type::UNKNOWN
    )
}

/// Raw cell bytes; decoded by column type in [`decode_row`].
struct Raw<'a>(&'a [u8]);

impl<'a> FromSql<'a> for Raw<'a> {
    fn from_sql(_ty: &Type, raw: &'a [u8]) -> Result<Self, BoxError> {
        Ok(Raw(raw))
    }

    fn accepts(_ty: &Type) -> bool {
        true
    }
}

fn fixed<const N: usize>(raw: &[u8]) -> Result<[u8; N], BoxError> {
    raw.try_into()
        .map_err(|_| format!("expected {N} bytes, got {}", raw.len()).into())
}

fn decode_cell(ty: &Type, raw: &[u8]) -> Result<Value, BoxError> {
    Ok(match *ty {
        Type::BOOL => Value::Integer(i64::from(raw.first().copied().unwrap_or(0) != 0)),
        Type::INT2 => Value::Integer(i64::from(i16::from_be_bytes(fixed(raw)?))),
        Type::INT4 => Value::Integer(i64::from(i32::from_be_bytes(fixed(raw)?))),
        Type::INT8 => Value::Integer(i64::from_be_bytes(fixed(raw)?)),
        Type::OID => Value::Integer(i64::from(u32::from_be_bytes(fixed(raw)?))),
        Type::FLOAT4 => Value::Real(f64::from(f32::from_be_bytes(fixed(raw)?))),
        Type::FLOAT8 => Value::Real(f64::from_be_bytes(fixed(raw)?)),
        Type::NUMERIC => Value::Decimal(numeric::decode(raw)?),
        Type::DATE => Value::Text(pgtime::days_to_date(i32::from_be_bytes(fixed(raw)?))),
        Type::TIMESTAMPTZ | Type::TIMESTAMP => {
            Value::Text(pgtime::micros_to_timestamp(i64::from_be_bytes(fixed(raw)?)))
        }
        Type::TIME => Value::Text(pgtime::micros_to_time(i64::from_be_bytes(fixed(raw)?))),
        Type::UUID => {
            let b = raw;
            if b.len() != 16 {
                return Err("invalid uuid length".into());
            }
            let hex: String = b.iter().map(|x| format!("{x:02x}")).collect();
            Value::Text(format!(
                "{}-{}-{}-{}-{}",
                &hex[0..8],
                &hex[8..12],
                &hex[12..16],
                &hex[16..20],
                &hex[20..32]
            ))
        }
        Type::JSONB => {
            Value::Text(String::from_utf8_lossy(raw.get(1..).unwrap_or_default()).into_owned())
        }
        Type::BYTEA => Value::Blob(raw.to_vec()),
        ref other if is_text(other) || *other == Type::JSON => {
            Value::Text(String::from_utf8_lossy(raw).into_owned())
        }
        _ => Value::Blob(raw.to_vec()),
    })
}

pub(crate) fn decode_row(row: &Row) -> Result<Vec<Value>, BoxError> {
    let mut cells = Vec::with_capacity(row.len());
    for (index, column) in row.columns().iter().enumerate() {
        let raw: Option<Raw<'_>> = row.try_get(index)?;
        cells.push(match raw {
            None => Value::Null,
            Some(Raw(bytes)) => decode_cell(column.type_(), bytes)?,
        });
    }
    Ok(cells)
}
