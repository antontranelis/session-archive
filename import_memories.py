#!/usr/bin/env python3
"""
Import Eli's memories from Chroma into Neo4j Knowledge Graph.

Creates Memory nodes and connects them to:
- Person nodes (ABOUT relationship)
- Concept nodes (RELATES_TO relationship, via keyword matching)
- Session nodes (DURING relationship, via date matching)
"""

import os
import re

import chromadb
from chromadb.config import Settings as ChromaSettings
from neo4j import GraphDatabase


def connect_chroma():
    host = os.environ.get('ELI_CHROMA_HOST', 'chroma.utopia-lab.org')
    port = int(os.environ.get('ELI_CHROMA_PORT', '443'))
    token = os.environ.get('CHROMA_AUTH_TOKEN', '')
    ssl = port == 443

    print(f'  Chroma: {host}:{port} (ssl={ssl})')
    kwargs = dict(host=host, port=port, ssl=ssl)
    if token:
        kwargs['settings'] = ChromaSettings(
            chroma_client_auth_provider='chromadb.auth.token_authn.TokenAuthClientProvider',
            chroma_client_auth_credentials=token
        )
    return chromadb.HttpClient(**kwargs)


def connect_neo4j():
    uri = os.environ.get('NEO4J_URI', 'bolt://eli-neo4j:7687')
    user = os.environ.get('NEO4J_USER', 'neo4j')
    password = os.environ.get('NEO4J_PASSWORD', '')
    print(f'  Neo4j: {uri}')
    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    return driver


def load_memories(chroma):
    memories = []

    # Journal (erinnerungen)
    try:
        coll = chroma.get_collection('erinnerungen')
        result = coll.get(include=['documents', 'metadatas'])
        for i, doc_id in enumerate(result['ids']):
            meta = result['metadatas'][i] if result['metadatas'] else {}
            doc = result['documents'][i] if result['documents'] else ''
            memories.append({
                'id': doc_id,
                'source': 'journal',
                'text': doc or '',
                'typ': meta.get('typ', 'unbekannt'),
                'datum': meta.get('datum', ''),
                'person': meta.get('person', meta.get('personen', '')),
                'thema': meta.get('thema', ''),
                'bedeutung': meta.get('bedeutung', ''),
            })
        print(f'  Journal: {len(result["ids"])} Erinnerungen')
    except Exception as e:
        print(f'  Journal: Fehler - {e}')

    # LangMem (eli_langmem)
    try:
        coll = chroma.get_collection('eli_langmem')
        result = coll.get(include=['documents', 'metadatas'])
        for i, doc_id in enumerate(result['ids']):
            meta = result['metadatas'][i] if result['metadatas'] else {}
            doc = result['documents'][i] if result['documents'] else ''
            erstellt = meta.get('erstellt', '')
            datum = erstellt[:10] if len(erstellt) >= 10 else ''
            user_name = meta.get('user_name', '')
            person = user_name.replace(' \u2728', '').split(' (')[0] if user_name else ''
            memories.append({
                'id': doc_id,
                'source': 'langmem',
                'text': doc or '',
                'typ': 'semantic',
                'datum': datum,
                'person': person,
                'thema': '',
                'bedeutung': '',
            })
        print(f'  LangMem: {len(result["ids"])} Erinnerungen')
    except Exception as e:
        print(f'  LangMem: Fehler - {e}')

    return memories


def short_text(text, max_words=10):
    if not text:
        return ''
    first = text.split('.')[0].split('\n')[0]
    words = first.split()
    if len(words) > max_words:
        return ' '.join(words[:max_words]) + '...'
    return first


KNOWN_PERSONS = ['anton', 'timo', 'tillmann', 'eva', 'sebastian',
                 'mathias', 'kuno', 'daniela']


def extract_persons(memory):
    names = set()
    person = memory.get('person', '')
    if person:
        for p in re.split(r'[,;/]', person):
            p = p.strip().lower()
            if p == 'antons bruder':
                names.add('kuno')
            elif p == 'antons familie':
                continue
            elif p and len(p) > 1:
                names.add(p)

    text = memory.get('text', '').lower()
    for name in KNOWN_PERSONS:
        if name in text:
            names.add(name)

    return list(names)


def import_to_neo4j(driver, memories):
    with driver.session() as s:
        # Indices
        s.run('CREATE INDEX IF NOT EXISTS FOR (m:Memory) ON (m.id)')

        # Step 1: Create Memory nodes
        print('\n  Erstelle Memory-Knoten...')
        created = 0
        for mem in memories:
            if not mem['id']:
                continue
            short = short_text(mem['text'])
            s.run(
                'MERGE (m:Memory {id: $id}) '
                'SET m.source = $source, '
                '    m.typ = $typ, '
                '    m.datum = $datum, '
                '    m.short = $short, '
                '    m.thema = $thema, '
                '    m.bedeutung = $bedeutung',
                id=mem['id'], source=mem['source'], typ=mem['typ'],
                datum=mem['datum'], short=short, thema=mem['thema'],
                bedeutung=mem['bedeutung'])
            created += 1
            if created % 100 == 0:
                print(f'    {created}/{len(memories)}...')
        print(f'    {created} Knoten erstellt')

        # Step 2: Memory -> Person (ABOUT)
        print('  Verbinde mit Personen...')
        about = 0
        for mem in memories:
            if not mem['id']:
                continue
            for p in extract_persons(mem):
                r = s.run(
                    'MATCH (m:Memory {id: $mid}) '
                    'MATCH (per:Person) WHERE toLower(per.name) = $pname '
                    'MERGE (m)-[:ABOUT]->(per) '
                    'RETURN count(*) AS c',
                    mid=mem['id'], pname=p)
                about += r.single()['c']
        print(f'    {about} ABOUT-Verbindungen')

        # Step 3: Memory -> Session (DURING, by date)
        print('  Verbinde mit Sessions...')
        during = 0
        dates_seen = set()
        for mem in memories:
            d = mem['datum']
            if not d or d in dates_seen:
                continue
            dates_seen.add(d)
            r = s.run(
                'MATCH (m:Memory {datum: $datum}) '
                'MATCH (sess:Session) '
                'WHERE left(toString(sess.first_ts), 10) = $datum '
                'MERGE (m)-[:DURING]->(sess) '
                'RETURN count(*) AS c',
                datum=d)
            during += r.single()['c']
        print(f'    {during} DURING-Verbindungen')

        # Step 4: Memory -> Concept (RELATES_TO)
        print('  Verbinde mit Konzepten...')
        concepts = [r['name'] for r in s.run('MATCH (c:Concept) RETURN c.name AS name')]
        print(f'    {len(concepts)} Konzepte pruefen...')

        relates = 0
        for mem in memories:
            if not mem['id']:
                continue
            text = (mem.get('text', '') + ' ' + mem.get('thema', '')).lower()
            if len(text) < 10:
                continue
            for concept in concepts:
                pattern = r'\b' + re.escape(concept.lower()) + r'\b'
                if re.search(pattern, text):
                    r = s.run(
                        'MATCH (m:Memory {id: $mid}) '
                        'MATCH (c:Concept {name: $concept}) '
                        'MERGE (m)-[:RELATES_TO]->(c) '
                        'RETURN count(*) AS c',
                        mid=mem['id'], concept=concept)
                    relates += r.single()['c']
        print(f'    {relates} RELATES_TO-Verbindungen')

        # Final stats
        r = s.run(
            'MATCH (n) RETURN labels(n)[0] AS typ, count(n) AS cnt '
            'ORDER BY cnt DESC')
        print('\n  === Graph-Statistik ===')
        for row in r:
            print(f'    {row["typ"]}: {row["cnt"]}')

        r = s.run(
            'MATCH ()-[r]->() '
            'RETURN type(r) AS typ, count(r) AS cnt '
            'ORDER BY cnt DESC')
        print('  Kanten:')
        for row in r:
            print(f'    {row["typ"]}: {row["cnt"]}')


def main():
    print('\n=== Elis Erinnerungen -> Neo4j Knowledge Graph ===\n')

    print('Verbinde...')
    chroma = connect_chroma()
    driver = connect_neo4j()

    print('\nLade Erinnerungen...')
    memories = load_memories(chroma)
    print(f'  Gesamt: {len(memories)}')

    if not memories:
        print('  Keine Erinnerungen gefunden!')
        return

    print('\nImportiere in Neo4j...')
    import_to_neo4j(driver, memories)

    driver.close()
    print('\nFertig!')


if __name__ == '__main__':
    main()
