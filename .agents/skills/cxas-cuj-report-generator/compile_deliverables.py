"""Unified GECX Master Ingestion Compiler for Dual Reports & Deliverables.

This script automates the compilation of both GECX reports (Comprehensive Full
and brief CUJ) and packages them into a zipped deliverables archive in one step.
"""

import argparse
import os
import subprocess
import sys


def run_command(cmd, cwd=None):
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ Error executing command: {result.stderr}", file=sys.stderr)
        sys.exit(result.returncode)
    print(result.stdout)


def compile_deliverables():
    parser = argparse.ArgumentParser(
        description="GECX Unified Deliverables Compiler"
    )
    parser.add_argument(
        "--transcripts_dir",
        default="/tmp/evals/customer_outputs",
        help="Directory containing GECX naturalized transcripts",
    )
    parser.add_argument(
        "--scratch_dir",
        default="/tmp/gecx_scratch",
        help="Permanent scratch deliverables directory",
    )
    parser.add_argument(
        "--outputs_dir",
        default="/tmp/evals/customer_outputs",
        help="Staging outputs directory",
    )

    args = parser.parse_args()

    skill_dir = os.path.dirname(os.path.abspath(__file__))
    construct_script = os.path.join(skill_dir, "construct_report.py")

    # Import compliance grader programmatically
    import pathlib

    sys.path.append(os.path.join(skill_dir, "evals/scripts/utils"))
    try:
        from grading import score_naturalness
    except ImportError as e:
        print(
            f"❌ Critical System Error: Failed to load GECX compliance grader: {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 0. Run Programmatic Quality Gate
    print("\n=== 0. RUNNING PROGRAMMATIC COMPLIANCE & NATURALNESS GATE ===")
    transcripts_path = pathlib.Path(args.transcripts_dir)
    files = list(transcripts_path.glob("*.yaml")) + list(
        transcripts_path.glob("*.yml")
    )

    linter_passed = True
    failed_files_count = 0

    for f in sorted(files):
        with open(f, "r") as file:
            content = file.read()

        scores = score_naturalness(content)
        for idx, (score, diagnostics) in enumerate(scores, 1):
            if score < 3:
                print(
                    f"  ❌ Compliance Failure: {f.name} (Subintent #{idx}) failed"
                    f" quality gate (Score: {score}.0/3.0)!"
                )
                for diag in diagnostics:
                    print(f"     💥 {diag}")
                linter_passed = False
                failed_files_count += 1

    if not linter_passed:
        print(
            "\n❌ COMPILATION ABORTED! Programmatic Gate rejected"
            f" {failed_files_count} files for styling/naming/payload failures.",
            file=sys.stderr,
        )
        print(
            "Please let the Ingestor Swarm self-heal these transcripts before"
            " proceeding.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"🟢 Programmatic Gate Passed! All {len(files)} transcripts verified"
        " successfully at perfect 3.0/3.0 scores."
    )

    temp_full_html = os.path.join(args.outputs_dir, "gecx_customer_report.html")
    temp_cuj_html = os.path.join(args.outputs_dir, "gecx_cuj_report.html")

    scratch_transcripts_dir = os.path.join(
        args.scratch_dir, "customer_campaign_deliverables/transcripts"
    )
    scratch_dashboard_dir = os.path.join(
        args.scratch_dir, "customer_campaign_deliverables/dashboard"
    )

    # 1. Compile Comprehensive Full Report
    print("\n=== 1. COMPILING COMPREHENSIVE FULL REPORT ===")
    cmd_full = [
        "python3",
        construct_script,
        f"--transcripts_dir={args.transcripts_dir}",
        f"--output_file={temp_full_html}",
        "--cuj_report=False",
        "--report_heading=Dining Service Order & Reservation CUJs",
        "--project_name=Dining Service Ingestion Campaign",
        "--index_title=Critical User Journeys Dashboard",
        (
            "--intro_context=Autonomous Ingestion & translation of Dining Service"
            " Order, Reservaton, and DFCX flows."
        ),
        (
            "--intro_goal=Compile high-fidelity natural dialogue transcripts with"
            " 100% quality verification."
        ),
        "--title=Dining Service CUJ Ingestion Report",
    ]
    run_command(cmd_full)

    # 2. Compile Brief CUJ Report
    print("\n=== 2. COMPILING BRIEF CUJ REPORT ===")
    cmd_cuj = [
        "python3",
        construct_script,
        f"--transcripts_dir={args.transcripts_dir}",
        f"--output_file={temp_cuj_html}",
        "--cuj_report=True",
        "--report_heading=Dining Service Order & Reservation CUJs (Brief)",
        "--project_name=Dining Service Ingestion Campaign",
        "--index_title=Core Critical User Journeys Dashboard",
        (
            "--intro_context=Brief executive dashboard displaying the 3 most"
            " critical conversational pathways."
        ),
        (
            "--intro_goal=Compile a brief visual report limiting examples to at"
            " most 3 core journeys."
        ),
        "--title=Dining Service Core CUJ Report",
    ]
    run_command(cmd_cuj)

    # 3. Port to Permanent Deliverables Workspace
    print("\n=== 3. PORTING TO PERMANENT DELIVERABLES FOLDER ===")
    os.makedirs(scratch_transcripts_dir, exist_ok=True)
    os.makedirs(scratch_dashboard_dir, exist_ok=True)

    # Copy YAML transcripts
    run_command(
        [
            "sh",
            "-c",
            f"cp -r {args.transcripts_dir}/*.yaml {scratch_transcripts_dir}/",
        ]
    )

    # Copy compiled HTML dashboards
    run_command(
        [
            "cp",
            "-r",
            temp_full_html,
            os.path.join(scratch_dashboard_dir, "gecx_customer_report.html"),
        ]
    )
    run_command(
        [
            "cp",
            "-r",
            temp_cuj_html,
            os.path.join(scratch_dashboard_dir, "gecx_cuj_report.html"),
        ]
    )
    print("Porting completed successfully!")

    # 4. Zipping Permanent Workspace
    print("\n=== 4. PACKAGING DELIVERABLES COMPRESSION ZIP ===")
    zip_path = os.path.join(
        args.outputs_dir, "customer_campaign_deliverables.zip"
    )
    if os.path.exists(zip_path):
        os.remove(zip_path)

    run_command(
        ["zip", "-r", "-q", zip_path, "customer_campaign_deliverables"],
        cwd=args.scratch_dir,
    )
    print(f"Zip package compiled successfully at: {zip_path}")

    print(
        "\n🎉 SUCCESS! Both GECX reports and the deliverables archive have been"
        " unified and locked successfully!"
    )


if __name__ == "__main__":
    compile_deliverables()
