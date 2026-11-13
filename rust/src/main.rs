#![forbid(unsafe_code)]
use cloudstick_watcher::{path_argument, run_worker, snapshot, validated_paths};
use std::process::ExitCode;
use std::time::Duration;

fn run() -> Result<(), &'static str> {
    let mut arguments = std::env::args().skip(1);
    let (mut staging, mut runner, mut config) = (None, None, None);
    let (mut interval, mut once, mut plan) = (10_u64, false, false);
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--staging" => staging = Some(path_argument(arguments.next())?),
            "--runner" => runner = Some(path_argument(arguments.next())?),
            "--config" => config = Some(path_argument(arguments.next())?),
            "--interval" => {
                interval = arguments
                    .next()
                    .and_then(|v| v.parse().ok())
                    .filter(|v| (5..=3600).contains(v))
                    .ok_or("invalid_interval")?
            }
            "--once" => once = true,
            "--plan" => plan = true,
            "--help" => {
                println!(
                    "cloudstick-watcher --staging ABSOLUTE_DIR --runner ABSOLUTE_PROGRAM --config ABSOLUTE_FILE [--interval 10] [--once] [--plan]"
                );
                return Ok(());
            }
            _ => return Err("unknown_argument"),
        }
    }
    let (staging, runner, config) = (
        staging.ok_or("staging_required")?,
        runner.ok_or("runner_required")?,
        config.ok_or("config_required")?,
    );
    if plan {
        println!(
            "{{\"source\":\"published_ready_batches\",\"mounts_images\":false,\"quiet_period_authorizes_reads\":false,\"executes_worker\":false}}"
        );
        return Ok(());
    }
    validated_paths(&staging, &runner, &config)?;
    let (mut previous, mut pending) = (None, false);
    loop {
        validated_paths(&staging, &runner, &config)?;
        let current = snapshot(&staging, &config)?;
        if current.candidate_count() > 0 && (pending || previous.as_ref() != Some(&current)) {
            match run_worker(&runner, &config) {
                Ok(report) => {
                    pending = report.needs_work();
                    println!(
                        "{{\"event\":\"worker_completed\",\"pending_jobs\":{},\"reconciliation_pending\":{}}}",
                        report.pending_jobs, report.reconciliation_pending
                    );
                }
                Err(code) => {
                    if once {
                        return Err(code);
                    }
                    pending = true;
                    eprintln!("{{\"error\":\"{code}\"}}");
                }
            }
        }
        previous = Some(current);
        if once {
            break;
        }
        std::thread::sleep(Duration::from_secs(interval));
    }
    Ok(())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(code) => {
            eprintln!("{{\"error\":\"{code}\"}}");
            ExitCode::FAILURE
        }
    }
}
