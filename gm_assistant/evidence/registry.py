from __future__ import annotations


class EvidenceRegistry:
    def __init__(self):
        self.providers = []

    def register(self, provider):
        self.providers.append(provider)

    def collect(
        self,
        question,
        candidate,
        owner_team_name,
        context,
    ):
        evidence = []

        for provider in self.providers:
            try:
                evidence.extend(
                    provider.collect(
                        question,
                        candidate,
                        owner_team_name,
                        context,
                    )
                )
            except Exception as e:
                print(f"[Evidence] {provider.name}: {e}")

        return evidence
