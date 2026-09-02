from auth import service_client
from services.sleeper_sync import refresh_sleeper_players


def main() -> None:
    refreshed = refresh_sleeper_players(
        service_client()
    )
    print(f"Refreshed {refreshed} Sleeper NFL players.")


if __name__ == "__main__":
    main()
