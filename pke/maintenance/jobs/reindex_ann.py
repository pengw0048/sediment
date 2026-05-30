"""Monthly ANN rebuild job."""

from pke.db.sqlite import SQLiteStore
from pke.identity.ann_index import AnnIndex
from pke.identity.resolver import blob_to_vector


def run(sqlite: SQLiteStore) -> AnnIndex:
    """Rebuild an ANN index from skill_nodes embeddings."""
    index = AnnIndex()
    rows = sqlite.conn.execute("SELECT id, embedding FROM skill_nodes").fetchall()
    for row in rows:
        vector = blob_to_vector(row["embedding"])
        if vector:
            index.add(str(row["id"]), vector)
    return index
