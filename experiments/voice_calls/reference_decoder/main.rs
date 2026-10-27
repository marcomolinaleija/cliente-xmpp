//! Independent Rust decoder of fixed synthetic packets exported by codecprobe.
//! No network, audio device, account or arbitrary input waveform.
mod mlow;
use std::io::{self, Read};

// Table-inflation adapter only: the codec itself is copied without changes.
extern crate self as wacore_binary;
pub mod zlib_pool {
    use std::io::Read;
    pub fn decompress_zlib_pooled(bytes: &[u8], limit: u64) -> std::io::Result<Vec<u8>> {
        let mut result = Vec::new();
        flate2::read::ZlibDecoder::new(bytes).take(limit + 1).read_to_end(&mut result)?;
        if result.len() as u64 > limit { return Err(std::io::Error::other("table exceeds limit")); }
        Ok(result)
    }
}

fn main() {
    let mut text = String::new();
    io::stdin().take(256 * 1024).read_to_string(&mut text).unwrap();
    let frames: Vec<String> = serde_json::from_str(&text).unwrap();
    assert_eq!(frames.len(), 84, "expected fixed voice-pause-voice test");
    let mut decoder = mlow::MlowDecoder::new();
    let (mut decoded, mut concealed, mut refused) = (0u32, 0u32, 0u32);
    let (mut before, mut after) = (0.0f64, 0.0f64);
    let mut tocs = std::collections::BTreeMap::<String, usize>::new();
    for (index, hex) in frames.iter().enumerate() {
        assert!(hex.len() % 2 == 0 && hex.len() <= 4096);
        let wire: Vec<u8> = (0..hex.len()).step_by(2)
            .map(|i| u8::from_str_radix(&hex[i..i+2], 16).unwrap()).collect();
        *tocs.entry(format!("{:02x}", wire[0])).or_default() += 1;
        let pcm = decoder.decode(&wire);
        let report = decoder.take_frame_report();
        decoded += u32::from(report.decoded);
        concealed += u32::from(report.concealed);
        refused += u32::from(report.off_point);
        assert_eq!(pcm.len(), 960);
        assert!(pcm.iter().all(|v| v.is_finite()));
        let energy: f64 = pcm.iter().map(|v| f64::from(*v).powi(2)).sum();
        if (6..16).contains(&index) { before += energy; }
        if (68..78).contains(&index) { after += energy; }
    }
    let ratio = after / before;
    // The reference reports the three 20 ms internal frames per 60 ms packet.
    let pass = decoded == 84 * 3 && concealed == 0 && refused == 0 &&
        before > 0.0 && (0.25..=4.0).contains(&ratio);
    println!("{}", serde_json::json!({
        "reference_commit": "d1d929aeb3ea9a10ee61cb87a3176b72e90f3dd2",
        "frames": frames.len(), "decoded_internal_frames": decoded, "concealed": concealed,
        "off_point": refused, "tocs": tocs, "post_pause_energy_ratio": ratio,
        "independent_decode_passed": pass, "live_call_verified": false
    }));
    assert!(pass, "independent decoder rejected frames or lost post-pause energy");
}
