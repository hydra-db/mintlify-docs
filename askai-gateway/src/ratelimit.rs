//! Per-client-IP sliding-window rate limiting (requests per minute).
//!
//! A single static binary serves many small sites, so the limiter is a simple
//! sharded-free in-memory window: exact, allocation-light, and good enough
//! for the abuse profile of a public docs widget (no Redis required).

use std::collections::HashMap;
use std::net::IpAddr;
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[derive(Debug)]
pub struct RateLimiter {
    max: u32,
    window: Duration,
    buckets: Mutex<HashMap<IpAddr, Vec<Instant>>>,
}

impl RateLimiter {
    pub fn new(max_per_minute: u32) -> Self {
        Self {
            max: max_per_minute,
            window: Duration::from_secs(60),
            buckets: Mutex::new(HashMap::new()),
        }
    }

    /// `max == 0` disables limiting entirely.
    pub fn disabled() -> Self {
        Self::new(0)
    }

    /// Record a request from `ip`; returns false when over the limit.
    pub fn allow(&self, ip: IpAddr) -> bool {
        if self.max == 0 {
            return true;
        }
        let mut buckets = self.buckets.lock().expect("rate limiter lock poisoned");
        let cutoff = Instant::now() - self.window;
        // Opportunistic hygiene: drop stale keys so the map stays small.
        if buckets.len() > 8_192 {
            buckets.retain(|_, v| v.last().map(|t| *t > cutoff).unwrap_or(false));
        }
        let bucket = buckets.entry(ip).or_default();
        bucket.retain(|t| *t > cutoff);
        if bucket.len() as u32 >= self.max {
            return false;
        }
        bucket.push(Instant::now());
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::Ipv4Addr;

    #[test]
    fn allows_under_limit_blocks_over() {
        let lim = RateLimiter::new(3);
        let ip = IpAddr::V4(Ipv4Addr::LOCALHOST);
        assert!(lim.allow(ip));
        assert!(lim.allow(ip));
        assert!(lim.allow(ip));
        assert!(!lim.allow(ip), "4th request within the window must be blocked");
        let other = IpAddr::V4(Ipv4Addr::new(127, 0, 0, 2));
        assert!(lim.allow(other), "limit is per-IP");
    }

    #[test]
    fn zero_disables_limiting() {
        let lim = RateLimiter::disabled();
        let ip = IpAddr::V4(Ipv4Addr::LOCALHOST);
        for _ in 0..100 {
            assert!(lim.allow(ip));
        }
    }

    #[test]
    fn window_recovers_over_time() {
        let lim = RateLimiter::new(1);
        let ip = IpAddr::V4(Ipv4Addr::LOCALHOST);
        assert!(lim.allow(ip));
        assert!(!lim.allow(ip));
        // Age the single entry beyond the window.
        let mut buckets = lim.buckets.lock().unwrap();
        let bucket = buckets.get_mut(&ip).unwrap();
        let stale = Instant::now() - Duration::from_secs(61);
        for t in bucket.iter_mut() {
            *t = stale;
        }
        drop(buckets);
        assert!(lim.allow(ip), "entries older than the window free capacity");
    }
}
