from auth import service_client


class PlayerProfileRepository:

    def __init__(self):
        self.sb = service_client()

    def player(self, name):

        rows = (
            self.sb.table("player_universe")
            .select("*")
            .ilike("player_name", f"%{name}%")
            .limit(1)
            .execute()
            .data
            or []
        )

        return rows[0] if rows else None
