"""Known competitions. Rows are created in the database the first time a competition is imported.

To add a competition: add it here, and make sure at least one provider adapter supports its code.
"""

COMPETITIONS: dict[str, dict[str, str]] = {
    "PL": {"name": "Premier League", "country": "England", "kind": "league"},
    "PD": {"name": "La Liga", "country": "Spain", "kind": "league"},
    "SA": {"name": "Serie A", "country": "Italy", "kind": "league"},
    "BL1": {"name": "Bundesliga", "country": "Germany", "kind": "league"},
    "FL1": {"name": "Ligue 1", "country": "France", "kind": "league"},
    "CL": {"name": "UEFA Champions League", "country": "Europe", "kind": "cup"},
}
