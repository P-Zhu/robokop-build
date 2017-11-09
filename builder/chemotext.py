#from greent.chemotext import Chemotext
#from greent.oxo import OXO
#from greent.service import GreenT
import json
import logging
from reasoner.graph_components import KEdge
from reasoner import node_types

def add_mesh( nodes ):
    pass

CHEMOTEXT_MESH_KEY = 'chemotext_mesh_label'

#TODO: where is code like this going to go?  Is it up to OXO? greent? something higher?  Built into the nodes?
def OXOdise_term(term):
    """Convert IRIs into CURIEs.
    
    Sometimes biolink or other sources will give an id like: http://purl.obolibrary.org/obo/OMIM_603903. 
    OXO expects: OMIM:603903.
    At least for the ones we've seen so far, the format has been that the last element in the url is
    the curie, but with : replaced by _."""
    if not term.startswith('http'):
        return term
    iri_term = term.split('/')[-1]
    if iri_term.count('_') != 1:
        logging.getLogger('application').warn('Invalid term for OXO: %s' % term)
        return term
    return ':'.join(iri_term.split('_'))

def convert_to_mesh(term,greent):
    """Use OXO to convert an id(doid, etc) to MeSH"""
    logging.getLogger('application').debug('convert_to_mesh %s' % term)
    #Check to see if we already have a MeSH curie
    if term[:5].upper() == 'MESH':
        return [term]
    #sometimes terms are not good for OXO, like http://purl.obolibrary.org/obo/OMIM_603903. OXO expects OMIM:603903
    term = OXOdise_term(term)
    oxo = greent.oxo
    response = oxo.query([ term ])
    #ugly, and probably wrong
    search_results = response['_embedded']['searchResults'][0]['mappingResponseList']
    meshes = []
    for result in search_results:
        if result['targetPrefix'] == 'MeSH':
            meshes.append( result )
            logging.getLogger('application').debug('  got %s(%s)' % (result['label'], result['curie']))
    if len(meshes) == 0:
        logging.getLogger('application').warn('No MeSH ID found for term: %s' % term)
        print('no mesh {}'.format(term))
    for mesh in meshes:
        add_chemotext_term(mesh, greent)
    return meshes

def add_chemotext_term(mesh_info,greent):
    """Look up the chemotext version of this mesh ID and attach it to the mesh structure.

    We retrive MESH ids from OXO and we get a dict back with a "curie" and a "label".
    Unfortunately, the label does not always match chemotext's "name".   The "label" does
    appear as a member of chemotext's "synonym" field.  So for a mesh id, we are going to 
    see if its a "name" in chemotext, and if not, then look for the name that has this as a
    synonym."""
    logging.getLogger('application').debug(' Check chemotext for %s' % mesh_info['label'])
    ctext = greent.chemotext
    label = mesh_info['label']
    #First, see if we get back anything using the term as a name
    response = ctext.query( query="MATCH (d:Term) WHERE d.name='%s' RETURN d" % (label))
    terms = []
    for result in response['results']:
        for data in result['data']:
            terms += data['row']
    if len(terms) > 0:
        mesh_info[CHEMOTEXT_MESH_KEY] = label
    else:
        #We didn't find anything, look for synonyms
        response = ctext.query( query="MATCH (d:Term) WHERE '%s' in d.synonyms RETURN d.name" % (label))
        names = []
        for result in response['results']:
            for data in result['data']:
                names += data['row']
        if len(names) == 1:
            mesh_info[CHEMOTEXT_MESH_KEY] = names[0]
        elif len(names) > 1:
            logging.getLogger('application').warn("Unusual amount of synonyms in chemotext for %s" % label)
            mesh_info[CHEMOTEXT_MESH_KEY] = names[0]
        else:
            logging.getLogger('application').warn("Cannot find chemotext synonym for %s" % label)
            mesh_info[CHEMOTEXT_MESH_KEY] = ''

def get_mesh_terms(node, greent):
    MESH_KEY = 'mesh_identifiers'
    if MESH_KEY not in node.properties:
        if node.node_type == node_types.DRUG:
            #TODO: Find a way (that is up-to-date) to actually get mesh terms for these guys.
            logging.getLogger('application').debug('Trying to use drug name: {} as MeSH term'.format(node.label.title()))
            mesh = {'curie':'', 'label':node.label.title()}
            add_chemotext_term( mesh, greent )
            node.add_mesh_identifier( mesh  )
        else:
            node.properties[MESH_KEY] =  convert_to_mesh( node.identifier, greent ) 
    return node.properties[MESH_KEY]

def term_to_term(node_a,node_b,greent,limit = 10000):
    """Given two terms, find articles in chemotext that connect them, and return as a KEdge.
    If nothing is found, return None"""
    print('Node: {}, Node: {}'.format(node_a.identifier, node_b.identifier))
    meshes_a = get_mesh_terms(node_a, greent)
    meshes_b = get_mesh_terms(node_b, greent)
    print('Meshes: {}, {}'.format(meshes_a, meshes_b))
    ctext = greent.chemotext
    articles=[]
    for ma in meshes_a:
        label_a = ma[CHEMOTEXT_MESH_KEY]
        for mb in meshes_b:
            label_b = mb[CHEMOTEXT_MESH_KEY]
            response = ctext.query( query="MATCH (d:Term)-[r1]-(a:Art)-[r2]-(t:Term) WHERE d.name='%s' AND t.name='%s' RETURN a LIMIT %d" % (label_a, label_b, limit))
            for result in response['results']:
                for data in result['data']:
                    articles += data['row']
    print('{} to {}: {}'.format(meshes_a, meshes_b, len(articles)))
    if len(articles) > 0:
        ke= KEdge( 'chemotext', 'term_to_term', { 'publications': articles }, is_support = True )
        ke.source_node = node_a
        ke.target_node = node_b
        return ke
    return None

def test_mesh_conversion(nodeid):
    from graph_components import KNode
    node = KNode(nodeid, 'D')
    get_mesh_terms(node)
    print(node.properties)
    get_mesh_terms(node)

def get_all():
    from greent.rosetta import Rosetta
    rosetta=Rosetta()
    ctext = rosetta.core.chemotext
    response = ctext.query( query="MATCH (d:Term) RETURN d")
    import json
    with open('chemotext.dump.json','w') as outfile:
        json.dump(response,outfile,indent=2)

def process_all():
    import json
    with open('chemotext.dump.json','r') as infile:
        ctext = json.load(infile)
    with open('chemotext.words.txt','w') as outfile:
        outfile.write('QUERY\tKEY\n')
        res = ctext['results'][0]
        n = 0
        for datum in res['data']:
            rows = datum['row']
            for row in rows:
                n+=1
                try:
                    rowtype = row['type']
                    meshname = row['name'].upper()
                    if 'synonyms' in row:
                        rowsyn  = row['synonyms']
                    else:
                        rowsyn = []
                    outfile.write('{}\t{}\n'.format(meshname, meshname))
                    for syn in rowsyn:
                        outfile.write('{}\t{}\n'.format(syn.upper(), meshname) )
                except KeyError:
                    print( json.dumps(row, indent=4) )
                    exit()

def read_synonyms():
    smap = {}
    with open('chemotext.words.txt','r') as infile:
        h = infile.readline()
        for line in infile:
            x = line.strip().split('\t')
            smap[x[0]] = x[1]
    print( len(smap) )

if __name__ == '__main__':
    #read_synonyms()
    #process_all()
    #test_mesh_conversion('DOID:1470')
    #add_chemotext_term({'label': 'Marble Bone Disease'})
    #print( term_to_term('DOID:4325', 'DOID:14504') )
    #print term_to_term('DOID:4325', 'http://www.orpha.net/ORDO/Orphanet_646')
