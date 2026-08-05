"""lbc — recherche leboncoin et détection de pépites, optimisée pour un usage agent.

Le principe directeur : tout le travail lourd (fetch, cohortes, statistiques,
extraction de modèle) se fait ici en Python. L'agent ne reçoit qu'une short-list
compacte. Voir README.md pour le budget de tokens.
"""

__version__ = "0.1.0"
