"""
Main entry point for the Polymarket trading agent.

Usage:
    python main.py              # Run the agent (paper trading by default)
    python main.py --once       # Run a single cycle then exit

Environment:
    Set PAPER_TRADING=false in .env for live trading (use with caution!)
"""

import os
import stat
import sys
import asyncio
import signal

from config.settings import PAPER_TRADING, INITIAL_BANKROLL, CYCLE_INTERVAL_SECONDS, _PROJECT_ROOT
from src.agent.orchestrator import Orchestrator
from src.utils.db import init_db
from src.utils.logger import setup_logger

logger = setup_logger("main")


def _check_env_permissions():
    """Check that .env file has restrictive permissions (owner-only read/write).

    If permissions are too open, tighten them to 600 and warn the user.
    This prevents other users on the system from reading API keys and private keys.
    """
    env_path = os.path.join(_PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return

    try:
        file_stat = os.stat(env_path)
        mode = file_stat.st_mode

        # Check if group or other can read/write/execute
        if mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP |
                   stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
            logger.warning(
                ".env file has overly permissive permissions (%s). "
                "Tightening to 600 (owner read/write only).",
                oct(mode & 0o777)
            )
            os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)  # 600
            logger.info(".env permissions set to 600")
        else:
            logger.debug(".env permissions OK: %s", oct(mode & 0o777))
    except OSError as e:
        logger.warning("Could not check .env permissions: %s", e)


def main():
    """Start the autonomous trading agent."""

    # Security: check .env file permissions before loading secrets
    _check_env_permissions()

    # Print startup banner
    mode = "PAPER TRADING" if PAPER_TRADING else "LIVE TRADING"
    logger.info("=" * 60)
    logger.info("  Polymarket Autonomous Trading Agent")
    logger.info("  Mode: %s", mode)
    logger.info("  Bankroll: $%.2f", INITIAL_BANKROLL)
    logger.info("  Cycle interval: %d seconds", CYCLE_INTERVAL_SECONDS)
    logger.info("=" * 60)

    if not PAPER_TRADING:
        logger.warning("LIVE TRADING MODE — real money is at risk!")
        logger.warning("Create data/STOP file to halt the agent at any time.")

        # Secondary confirmation for live trading (H-2)
        if sys.stdin.isatty():
            confirm = input(
                "\n  [!] You are about to start LIVE TRADING with real money.\n"
                "  Type 'yes' to confirm: "
            ).strip().lower()
            if confirm != "yes":
                logger.info("Live trading not confirmed — exiting.")
                sys.exit(0)

    # Create orchestrator
    agent = Orchestrator()

    # Handle graceful shutdown (Ctrl+C)
    def shutdown_handler(signum, frame):
        logger.info("Shutdown signal received — cleaning up...")
        agent.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Run the agent
    try:
        if "--once" in sys.argv:
            logger.info("Running single cycle (--once flag)")
            async def _run_once():
                from src.trading.portfolio import Portfolio
                await init_db()
                restored = await Portfolio.load_from_db()
                if restored:
                    agent.portfolio = restored
                    agent.executor.portfolio = restored
                    logger.info("Portfolio restored from previous session")
                await agent.run_cycle()
            asyncio.run(_run_once())
        else:
            asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        agent.shutdown()


if __name__ == "__main__":
    main()
