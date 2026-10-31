"""Import the existing JSON state file into an empty PostgreSQL database."""
import argparse

from dotenv import load_dotenv

load_dotenv()

from . import store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sync-local",
        action="store_true",
        help="Back up existing PostgreSQL state, then replace it with the local state file",
    )
    parser.add_argument(
        "--state-file",
        default=str(store.STATE_FILE),
        help="Path to the existing state.json (defaults to backend/state/state.json)",
    )
    args = parser.parse_args()
    if args.sync_local:
        store.sync_state_file(args.state_file)
        print(f"Backed up PostgreSQL state and synced scheduler state from {args.state_file}")
    else:
        store.import_state_file(args.state_file)
        print(f"Imported scheduler state from {args.state_file}")


if __name__ == "__main__":
    main()
