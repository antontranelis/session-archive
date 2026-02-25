#!/usr/bin/env python3
"""Add full text to existing Memory nodes in Neo4j from Chroma."""
import os
import chromadb
from chromadb.config import Settings
from neo4j import GraphDatabase

# Chroma (external)
chroma_host = os.environ.get('ELI_CHROMA_HOST', 'chroma.utopia-lab.org')
chroma_port = int(os.environ.get('ELI_CHROMA_PORT', '443'))
chroma_token = os.environ.get('CHROMA_AUTH_TOKEN', '')

chroma = chromadb.HttpClient(
    host=chroma_host, port=chroma_port, ssl=True,
    settings=Settings(
        chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
        chroma_client_auth_credentials=chroma_token
    )
)

# Neo4j
neo4j_uri = os.environ.get('NEO4J_URI', 'bolt://eli-neo4j:7687')
neo4j_user = os.environ.get('NEO4J_USER', 'neo4j')
neo4j_pass = os.environ.get('NEO4J_PASSWORD', '')
driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))

# Load all texts from Chroma
texts = {}  # id -> full text

for coll_name in ['erinnerungen', 'eli_langmem']:
    try:
        coll = chroma.get_collection(coll_name)
        result = coll.get(include=['documents'])
        for i, doc_id in enumerate(result['ids']):
            doc = result['documents'][i] if result['documents'] else ''
            if doc:
                texts[doc_id] = doc
        print(f'{coll_name}: {len(result["ids"])} loaded')
    except Exception as e:
        print(f'{coll_name}: error - {e}')

print(f'\nTotal texts: {len(texts)}')

# Update Neo4j
with driver.session() as s:
    updated = 0
    for mem_id, text in texts.items():
        s.run(
            'MATCH (m:Memory {id: $id}) SET m.text = $text',
            id=mem_id, text=text
        )
        updated += 1
        if updated % 100 == 0:
            print(f'  {updated}/{len(texts)}...')
    print(f'\n{updated} Memory nodes updated with full text')

driver.close()
