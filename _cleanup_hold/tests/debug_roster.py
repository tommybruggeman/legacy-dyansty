from supabase import create_client

# --- Supabase config ---
SUPABASE_URL = "https://fhyaahtdbqbdbbspynye.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZoeWFhaHRkYnFiZGJic3B5bnllIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjEyNjQ0NDEsImV4cCI6MjA3Njg0MDQ0MX0.PCE_Tz3YMtBmGH09Kf9A1dpP_9BwYfEhPswoyWJ9wpU"

# --- League to inspect ---
LEAGUE_ID = "125743535489026048"  # no trailing spaces

def main():
    # Create Supabase client
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # 1) Check roster rows
    resp = supabase.table("roster").select("*").eq("league_id", LEAGUE_ID).limit(10).execute()
    print("Roster rows for league:", len(resp.data))
    for row in resp.data:
        print(row)

    # 2) Check teams rows (if table/view exists)
    try:
        t_resp = supabase.table("teams").select("*").eq("league_id", LEAGUE_ID).limit(10).execute()
        print("Team rows for league:", len(t_resp.data))
        for row in t_resp.data:
            print(row)
    except Exception as e:
        print("Error querying teams table:", e)

if __name__ == "__main__":
    main()

