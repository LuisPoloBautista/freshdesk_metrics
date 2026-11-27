"""Deterministic aliases for institutions; identity-based grouping for contacts."""
from collections import defaultdict
import unicodedata


def normalized_name(value):
    text = unicodedata.normalize('NFKD', str(value or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in text).split())


class InstitutionIndex:
    def __init__(self, companies, local_institutions=()):
        self.names, self.aliases, self.by_id = {}, defaultdict(set), {}
        # Canonical IDs and explicit alternate company IDs take precedence.
        canonical = [c for c in companies.values() if c.get('id_aliases')]
        remaining = [c for c in companies.values() if not c.get('id_aliases')]
        for c in canonical + remaining:
            cid = str(c['id'])
            key = self.by_id.get(cid, 'company:' + cid)
            if key not in self.names:
                self.names[key] = c['name']
            self.by_id[cid] = key
            for alternate in c.get('id_aliases', []):
                self.by_id[str(alternate)] = key
            for name in [c['name'], *(c.get('aliases') or [])]:
                self.aliases[normalized_name(name)].add(key)
        for c in local_institutions:
            key = 'name:' + normalized_name(c['name'])
            self.names[key] = c['name']
            for name in [c['name'], *c.get('aliases', [])]:
                self.aliases[normalized_name(name)].add(key)

    def label(self, name):
        normalized = normalized_name(name)
        if normalized in ('', 'n a', 'na', 'none', 'null', 'sin institucion', 'sin empresa', 'no aplica'):
            return ''
        keys = self.aliases.get(normalized, set())
        if len(keys) == 1:
            return next(iter(keys))
        key = 'name:' + normalized
        self.names.setdefault(key, str(name).strip())
        if not keys:
            self.aliases[normalized].add(key)
        return key

    def company(self, cid):
        if not cid:
            return ''
        key = self.by_id.get(str(cid), 'company:' + str(cid))
        self.names.setdefault(key, 'Institución ID ' + str(cid))
        return key

    def add_remote_company(self, company):
        cid = str(company['id'])
        if cid in self.by_id:
            return self.by_id[cid]
        candidates = self.aliases.get(normalized_name(company['name']), set())
        key = next(iter(candidates)) if len(candidates) == 1 else 'company:' + cid
        self.by_id[cid] = key
        if key not in self.names or self.names[key] == 'Institución ID ' + cid:
            self.names[key] = company['name']
        self.aliases[normalized_name(company['name'])].add(key)
        return key


def contact_groups(contacts, verified_groups=()):
    """Same exact email/external ID or explicit IDs may group; names never suffice."""
    parent = {uid: uid for uid in contacts}

    def find(uid):
        while uid != parent[uid]:
            parent[uid] = parent[parent[uid]]
            uid = parent[uid]
        return uid

    def union(ids):
        ids = sorted({str(uid) for uid in ids if str(uid) in parent})
        for uid in ids[1:]:
            parent[find(uid)] = find(ids[0])

    for field in ['email', 'unique_external_id']:
        seen = defaultdict(list)
        for uid, c in contacts.items():
            values = [c.get(field)] + ((c.get('other_emails') or []) if field == 'email' else [])
            for value in values:
                if value and str(value).strip():
                    # External identifiers are case-sensitive; email addresses are not.
                    value = str(value).strip().casefold() if field == 'email' else str(value).strip()
                    seen[value].append(uid)
        for ids in seen.values():
            union(ids)
    for group in verified_groups:
        union(group['contact_ids'])
    grouped = defaultdict(list)
    for uid in contacts:
        grouped[find(uid)].append(uid)
    result = {}
    for key, ids in grouped.items():
        ids = sorted(ids)
        names = sorted({str(contacts[i].get('name') or '').strip() for i in ids if contacts[i].get('name')})
        emails = sorted({str(contacts[i].get('email') or '') for i in ids if contacts[i].get('email')})
        result[key] = {'id': key, 'contact_ids': ids, 'name': names[0] if names else f'Contacto {key}',
                       'name_variants': names, 'emails': emails,
                       'institution_keys': sorted({k for i in ids for k in contacts[i].get('institution_keys', [])})}
    return result
