"""
aqua_rest_client.py
====================
Python client for the AQUA REST API.
API docs: https://wiki.ith.intel.com/spaces/AQUA/pages/3129508713/AQUA+REST+API

Usage (standalone):
    python aqua_rest_client.py \\
        --user snpant \\
        --report "path/to/report_config.txt" \\
        --outputpath "\\\\server\\share\\aqua_output" \\
        --outfile "result.csv.gz"

Usage (as module):
    from aqua_rest_client import AquaRestClient
    client = AquaRestClient(user_id="snpant")
    job_id = client.execute(report_txt_path="report.txt", output_path="\\\\server\\share")
    result = client.wait_for_job(job_id)
    print(result)  # {"jobId": ..., "status": "Completed", "sharedPath": ...}
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import ssl
import time
from pathlib import Path
from typing import Optional

import requests
import urllib3

# ── Configuration ─────────────────────────────────────────────────────────────
AQUA_BASE_URL = "https://aqua-api.intel.com"
# Intel internal SSL cert — download from wiki if needed
# https://wiki.ith.intel.com/spaces/AQUA/pages/3129508713/AQUA+REST+API
INTEL_CERT = os.environ.get("INTEL_CHAIN_PEM", "IntelChain.pem")

# Poll interval / max wait
DEFAULT_POLL_INTERVAL_S = 15
DEFAULT_MAX_WAIT_S = 3600  # 1 hour


class AquaRestClient:
    """Thin wrapper around the AQUA REST API."""

    def __init__(
        self,
        user_id: str,
        base_url: str = AQUA_BASE_URL,
        cert: str | bool = True,
        proxies: Optional[dict] = None,
        poll_interval_s: int = DEFAULT_POLL_INTERVAL_S,
        max_wait_s: int = DEFAULT_MAX_WAIT_S,
    ):
        """
        Parameters
        ----------
        user_id       : IDSID, e.g. "snpant"
        base_url      : API root URL
        cert          : Path to IntelChain.pem, True (system), or False (skip verify — not recommended)
        proxies       : e.g. {"https": "http://proxy-us.intel.com:911"}
        poll_interval_s : seconds between status polls
        max_wait_s    : max seconds to wait for job completion
        """
        self.user_id = user_id
        self.base_url = base_url.rstrip("/")
        self.poll_interval_s = poll_interval_s
        self.max_wait_s = max_wait_s

        # Resolve cert
        if isinstance(cert, str) and not Path(cert).exists():
            print(f"[AquaRestClient] Warning: cert file not found at '{cert}', falling back to system CA")
            cert = True
        self._cert = cert

        self._session = requests.Session()
        self._session.verify = self._cert
        if proxies:
            self._session.proxies.update(proxies)

    # ── Low-level API calls ────────────────────────────────────────────────────

    def execute(
        self,
        report_txt_path: str | Path,
        output_path: str,
        email_notification: bool = True,
        format: str = "csv.gz",
    ) -> int:
        """
        POST /api/query/execute/user/{userId}
        Submit a job to run data extraction. Returns jobId.

        Parameters
        ----------
        report_txt_path   : path to exported AQUA report config .txt file
        output_path       : network/shared path where output will be written
                            (must be accessible by 'aquajobs' service account)
        email_notification: whether AQUA sends completion email
        format            : output format hint (csv.gz or parquet) — passed as outputpath suffix
        """
        report_txt_path = Path(report_txt_path)
        if not report_txt_path.exists():
            raise FileNotFoundError(f"Report config not found: {report_txt_path}")

        url = f"{self.base_url}/api/query/execute/user/{self.user_id}"
        params = {"emailNotification": "YES" if email_notification else "NO"}

        with report_txt_path.open("rb") as f:
            files = {"file": (report_txt_path.name, f, "text/plain")}
            data = {"outputpath": output_path}
            resp = self._session.post(url, params=params, files=files, data=data, timeout=60)

        resp.raise_for_status()
        job_id = int(resp.text.strip())
        print(f"[execute] Job submitted: jobId={job_id}")
        return job_id

    def generate(self, report_txt_path: str | Path) -> int:
        """
        POST /api/query/generate/user/{userId}
        Generate Midas HBase query only (no extraction). Returns jobId.
        """
        report_txt_path = Path(report_txt_path)
        if not report_txt_path.exists():
            raise FileNotFoundError(f"Report config not found: {report_txt_path}")

        url = f"{self.base_url}/api/query/generate/user/{self.user_id}"
        with report_txt_path.open("rb") as f:
            files = {"file": (report_txt_path.name, f, "text/plain")}
            resp = self._session.post(url, files=files, timeout=60)

        resp.raise_for_status()
        job_id = int(resp.text.strip())
        print(f"[generate] Job submitted: jobId={job_id}")
        return job_id

    def get_status(self, job_id: int) -> str:
        """
        GET /api/job/{jobId}/status
        Returns one of: Pending, Completed, Fail
        """
        url = f"{self.base_url}/api/job/{job_id}/status"
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text.strip()

    def get_status_full(self, job_id: int) -> dict:
        """
        GET /api/job/{jobId}/status/full
        Returns dict: {jobId, status, message, sharedPath}
        """
        url = f"{self.base_url}/api/job/{job_id}/status/full"
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_query_json(self, job_id: int) -> str:
        """
        GET /api/query/get/user/{userId}/jobId/{jobId}
        Returns the generated Midas HBase JSON query (after job completes).
        """
        url = f"{self.base_url}/api/query/get/user/{self.user_id}/jobId/{job_id}"
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text

    # ── High-level helpers ─────────────────────────────────────────────────────

    def wait_for_job(self, job_id: int) -> dict:
        """
        Poll GET /api/job/{jobId}/status/full until Completed or Fail.
        Returns the full status dict.
        Raises RuntimeError on Fail or timeout.
        """
        elapsed = 0
        print(f"[wait_for_job] Polling jobId={job_id} every {self.poll_interval_s}s ...")
        while elapsed < self.max_wait_s:
            result = self.get_status_full(job_id)
            status = result.get("status", "").lower()
            print(f"  [{elapsed:>5}s] status={result.get('status')}  message={result.get('message', '')}")
            if status == "completed":
                print(f"[wait_for_job] Job {job_id} completed. sharedPath={result.get('sharedPath')}")
                return result
            if status == "fail":
                raise RuntimeError(f"Job {job_id} failed: {result.get('message')}")
            time.sleep(self.poll_interval_s)
            elapsed += self.poll_interval_s

        raise TimeoutError(f"Job {job_id} did not complete within {self.max_wait_s}s")

    def run_and_download(
        self,
        report_txt_path: str | Path,
        output_path: str,
        local_dest: str | Path | None = None,
        email_notification: bool = False,
    ) -> Path | None:
        """
        Full pipeline: submit → poll → (optionally copy file locally).

        Parameters
        ----------
        report_txt_path : AQUA report .txt config
        output_path     : network path AQUA writes result to
                          (grant aquajobs write access)
        local_dest      : if provided, copies the result file(s) here
        email_notification : send email on completion

        Returns
        -------
        Path to local copy if local_dest provided, else None.
        """
        job_id = self.execute(report_txt_path, output_path, email_notification)
        result = self.wait_for_job(job_id)
        shared_path = result.get("sharedPath")

        if local_dest and shared_path:
            local_dest = Path(local_dest)
            local_dest.mkdir(parents=True, exist_ok=True)
            shared = Path(shared_path)
            if shared.is_file():
                dest_file = local_dest / shared.name
                shutil.copy2(shared, dest_file)
                print(f"[run_and_download] Copied to {dest_file}")
                return dest_file
            elif shared.is_dir():
                # Copy all files in the output folder
                copied = []
                for f in shared.iterdir():
                    dest_file = local_dest / f.name
                    shutil.copy2(f, dest_file)
                    copied.append(dest_file)
                print(f"[run_and_download] Copied {len(copied)} file(s) to {local_dest}")
                return local_dest
            else:
                print(f"[run_and_download] sharedPath not accessible: {shared_path}")

        return None


# ── CLI ────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="AQUA REST API client — submit a report and download the result."
    )
    p.add_argument("--user",        required=True,  help="IDSID (e.g. snpant)")
    p.add_argument("--report",      required=True,  help="Path to exported AQUA report .txt config")
    p.add_argument("--outputpath",  required=True,  help="Network path AQUA writes output to (accessible by aquajobs)")
    p.add_argument("--outdir",      default=None,   help="Local dir to copy result into (optional)")
    p.add_argument("--url",         default=AQUA_BASE_URL, help=f"API base URL (default: {AQUA_BASE_URL})")
    p.add_argument("--cert",        default=INTEL_CERT,    help="Path to IntelChain.pem (default: env INTEL_CHAIN_PEM or IntelChain.pem)")
    p.add_argument("--no-email",    action="store_true",   help="Disable completion email notification")
    p.add_argument("--poll",        type=int, default=DEFAULT_POLL_INTERVAL_S, help="Poll interval in seconds (default: 15)")
    p.add_argument("--timeout",     type=int, default=DEFAULT_MAX_WAIT_S,      help="Max wait seconds (default: 3600)")
    p.add_argument("--proxy",       default=os.environ.get("HTTPS_PROXY"),     help="HTTPS proxy (default: HTTPS_PROXY env var)")
    p.add_argument("--status",      type=int, metavar="JOBID", help="Just check status of an existing job ID and exit")
    return p


def main():
    args = _build_parser().parse_args()

    proxies = {"https": args.proxy, "http": args.proxy} if args.proxy else None
    cert = args.cert if Path(args.cert).exists() else True

    client = AquaRestClient(
        user_id=args.user,
        base_url=args.url,
        cert=cert,
        proxies=proxies,
        poll_interval_s=args.poll,
        max_wait_s=args.timeout,
    )

    # Status-only mode
    if args.status:
        result = client.get_status_full(args.status)
        print(result)
        return

    # Full run
    client.run_and_download(
        report_txt_path=args.report,
        output_path=args.outputpath,
        local_dest=args.outdir,
        email_notification=not args.no_email,
    )


if __name__ == "__main__":
    main()
