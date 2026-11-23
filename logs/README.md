# logs/

Journaux CSV écrits par les nœuds à l'exécution, un fichier par nœud.

Format : `ts_ns, node, thread, corr_id, event, detail`.

`logs/rt/<nœud>_jobs.csv` contient les mesures par job de T1 à T5, écrites à l'arrêt des nœuds et résumées par `python -m rt.report`.
