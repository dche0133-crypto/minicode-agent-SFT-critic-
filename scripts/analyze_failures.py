import argparse
import json
from pathlib import Path

from scripts.failure_analysis import attach_failure_analysis


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Attach failure analysis to a benchmark result JSON file.")
    parser.add_argument("report", help="Path to benchmark result JSON.")
    parser.add_argument("--benchmarks", default=None, help="Benchmark root directory; defaults to the report value.")
    parser.add_argument("--out", default=None, help="Output path; defaults to overwriting the report.")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    report_path = Path(args.report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    attach_failure_analysis(report, benchmark_root=args.benchmarks)
    out_path = Path(args.out) if args.out else report_path
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out_path}")
    print(json.dumps(report.get("summary", {}).get("failure_types", {}), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
