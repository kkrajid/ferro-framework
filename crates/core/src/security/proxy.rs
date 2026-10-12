//! Client address resolution behind trusted reverse proxies (research R6).

use std::net::IpAddr;

#[derive(Debug, Clone, Default)]
pub struct TrustedProxies {
    networks: Vec<(IpAddr, u8)>,
}

fn bits(address: IpAddr) -> (u128, u8) {
    match address.to_canonical() {
        IpAddr::V4(v4) => (u128::from(u32::from(v4)), 32),
        IpAddr::V6(v6) => (u128::from(v6), 128),
    }
}

impl TrustedProxies {
    /// Addresses (`10.0.0.1`, `::1`) and networks (`10.0.0.0/8`, `fd00::/8`).
    pub fn parse(entries: &[String]) -> Result<Self, String> {
        let mut networks = Vec::with_capacity(entries.len());
        for entry in entries {
            let entry = entry.trim();
            let invalid = || format!("'{entry}' is not an IP address or network (CIDR)");
            let (address, prefix) = match entry.split_once('/') {
                Some((address, prefix)) => (address, Some(prefix)),
                None => (entry, None),
            };
            let address: IpAddr = address.parse().map_err(|_| invalid())?;
            let width = bits(address).1;
            let prefix = match prefix {
                Some(prefix) => prefix.parse::<u8>().map_err(|_| invalid())?,
                None => width,
            };
            if prefix > width {
                return Err(invalid());
            }
            networks.push((address.to_canonical(), prefix));
        }
        Ok(Self { networks })
    }

    pub fn is_empty(&self) -> bool {
        self.networks.is_empty()
    }

    pub fn is_trusted(&self, address: IpAddr) -> bool {
        let (value, width) = bits(address);
        self.networks.iter().any(|(network, prefix)| {
            let (base, base_width) = bits(*network);
            if base_width != width {
                return false;
            }
            let shift = u32::from(width - prefix);
            if shift >= 128 {
                return true;
            }
            (value >> shift) == (base >> shift)
        })
    }

    /// The client's address: `peer`, or — when `peer` is a trusted proxy — the right-most
    /// untrusted address of `x-forwarded-for`.
    pub fn client(&self, peer: IpAddr, forwarded_for: Option<&str>) -> IpAddr {
        if !self.is_trusted(peer) {
            return peer;
        }
        let Some(forwarded) = forwarded_for else {
            return peer;
        };
        let mut last = peer;
        for part in forwarded.rsplit(',') {
            let Ok(address) = part.trim().parse::<IpAddr>() else {
                return last;
            };
            if !self.is_trusted(address) {
                return address;
            }
            last = address;
        }
        last
    }
}
