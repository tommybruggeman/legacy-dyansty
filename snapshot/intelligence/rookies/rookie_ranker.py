from snapshot.intelligence.ai.player_dossier import PlayerDossierBuilder


def rank_rookie_board(rows):
    return PlayerDossierBuilder().enrich_rows(rows, mode="rookie")
