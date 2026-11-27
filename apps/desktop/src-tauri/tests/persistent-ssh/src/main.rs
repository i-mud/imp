mod authority;
mod gate;
mod raw;
mod session;
mod wire;

use std::io::{self, BufRead, Read, Write};
use std::time::Duration;

use anyhow::{ensure, Context, Result};
use serde_json::Value;
use tokio::sync::{mpsc, oneshot};

const MAX_LINE_BYTES: usize = 64 * 1024;

// Authority jobs share one runtime thread. Generation checks, mutations, and
// output enqueueing without an await run together, before any replacement task.
#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    let mut args = std::env::args_os().skip(1);
    match args.next() {
        Some(mode) if mode == "raw" => raw::run(args.collect()).await,
        Some(mode) if mode == "measure" => raw::measure(args.collect()).await,
        Some(mode) if mode != "authority" => {
            anyhow::bail!("expected authority, raw or measure mode")
        }
        _ => {
            ensure!(
                args.next().is_none(),
                "authority mode does not take arguments"
            );
            run_authority().await
        }
    }
}

async fn run_authority() -> Result<()> {
    let (input_tx, input_rx) = mpsc::channel(16);
    let (output_tx, mut output_rx) = mpsc::channel::<Value>(64);
    let (flushed_tx, flushed_rx) = oneshot::channel();

    // Blocking pipe reads/writes must not outlive the async shutdown deadline.
    std::thread::spawn(move || {
        let mut stdin = io::stdin().lock();
        loop {
            let mut line = Vec::new();
            let read = (&mut stdin)
                .take((MAX_LINE_BYTES + 1) as u64)
                .read_until(b'\n', &mut line);
            let entry = match read {
                Ok(0) => break,
                Ok(_) if line.len() <= MAX_LINE_BYTES => Ok(line),
                Ok(_) => Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "fixture request line too large",
                )),
                Err(error) => Err(error),
            };
            let done = entry.is_err();
            if input_tx.blocking_send(entry).is_err() || done {
                break;
            }
        }
    });
    std::thread::spawn(move || {
        let written = (|| -> io::Result<()> {
            let mut stdout = io::stdout().lock();
            while let Some(line) = output_rx.blocking_recv() {
                serde_json::to_writer(&mut stdout, &line)?;
                writeln!(stdout)?;
                stdout.flush()?;
            }
            Ok(())
        })();
        let _ = flushed_tx.send(written);
    });

    let result = authority::run(input_rx, output_tx).await;
    tokio::time::timeout(Duration::from_secs(2), flushed_rx)
        .await
        .context("fixture output flush timed out")???;
    result
}
