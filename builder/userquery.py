from greent.node_types import node_types, DRUG_NAME, DISEASE_NAME, UNSPECIFIED
from greent.util import Text


class Transition:
    def __init__(self, last_type, next_type, min_path_length, max_path_length):
        self.in_type = last_type
        self.out_type = next_type
        self.min_path_length = min_path_length
        self.max_path_length = max_path_length
        self.in_node = None
        self.out_node = None

    def generate_reverse(self):
        return Transition(self.out_type, self.in_type, self.min_path_length, self.max_path_length)

    @staticmethod
    def get_fstring(ntype):
        if ntype == DRUG_NAME or ntype == DISEASE_NAME:
            return 'n{0}{{name:"{1}"}}'
        if ntype is None:
            return 'n{0}'
        else:
            return 'n{0}:{1}'

    def generate_concept_cypher_pathstring(self, t_number):
        start = f'(n{t_number}:Concept)'
        end = f'(n{t_number+1}:Concept)'
        if self.max_path_length > 1:
            pstring = f'p{t_number}=allShortestPaths({start}-[:translation*{self.min_path_length}..{self.max_path_length}]->{end})'
        else:
            pstring = f'p{t_number}={start}-[:translation]->{end}'
        return pstring

    def generate_cypher_pathstring(self, t_number):
        fstring = self.get_fstring(self.in_type)
        self.in_node = fstring.format(t_number, self.in_type)
        fstring = self.get_fstring(self.out_type)
        self.out_node = fstring.format(t_number + 1, self.out_type)
        pstring = 'p{0}=({1})-[*{2}..{3}]->({4})'
        # TODO: how to set max path here.  It's our max_path, plus some slop for synonyms.
        # Assuming 1 synonym per translation (and some slop?)?
        return pstring.format(t_number, self.in_node, self.min_path_length, 2 * self.max_path_length + 2, self.out_node)

    def generate_cypher_withstring(self, t_number, pathstring):
        withline = 'WITH {}'.format(pathstring)
        for i in range(t_number):
            withline += ', d{0}, Syn{0}'.format(i)
        withline += ',\n'
        withline += '''reduce(weight=0, r in relationships(p{0}) | CASE type(r) WHEN "SYNONYM" THEN weight ELSE weight + 1 END ) as d{0},
reduce(weight=0, r in relationships(p{0}) | CASE type(r) WHEN "SYNONYM" THEN weight + 1 ELSE weight END ) as Syn{0}'''.format(
            t_number)
        if self.min_path_length == self.max_path_length:
            withline += '\nWHERE d{0} = {1}'.format(t_number, self.min_path_length)
        else:
            withline += '\nWHERE d{0} >= {1} AND d{0} <= {2}'.format(t_number, self.min_path_length,
                                                                     self.max_path_length)
        return withline


class QueryDefinition:
    """Defines a query"""

    def __init__(self):
        self.start_values = None
        self.start_type = None
        self.end_values = None
        self.node_types = []
        self.transitions = []
        self.start_lookup_node = None
        self.end_lookup_node = None

    def generate_left_query_def(self, i):
        """Generate a query definition starting at the left and moving in i steps
           input: 0 <= i < len(transitions) """
        if i < 0 or i > len(self.transitions):
            raise ValueError("Invalid break point: {}".format(i))
        left_def = QueryDefinition()
        left_def.start_values = self.start_values
        left_def.start_type = self.start_type
        left_def.node_types = self.node_types[:i + 1]
        left_def.transitions = self.transitions[:i]
        left_def.start_lookup_node = self.start_lookup_node
        return left_def

    def generate_right_query_def(self, i):
        """Generate a query definition starting at the right and going to a node i 
           steps from the left end. 
           input: 0 < i < len(transitions) 
           output: A query definition complementary to generate_left_query_def(i)
           """
        if i < 0 or i > len(self.transitions):
            raise ValueError("Invalid break point: {}".format(i))
        right_def = QueryDefinition()
        right_def.start_values = self.end_values
        right_def.start_type = self.node_types[-1]
        right_def.node_types = self.node_types[i:]
        right_def.transitions = self.transitions[i:]
        right_def.start_lookup_node = self.end_lookup_node
        right_def.node_types.reverse()
        right_def.transitions.reverse()
        right_def.transitions = [t.generate_reverse() for t in right_def.transitions]
        return right_def

    def generate_paired_query(self, i):
        """From a Two Sided Query Definition, create two One Sided Query Definitions.
        The split is performed at the i-th node, which must occur in both sides."""
        return self.generate_paired_query_sharing_node(i) + self.generate_paired_query_splitting_transition(i)

    def generate_paired_query_splitting_transition(self, i):
        """Split along a transitions if in contains untyped nodes"""
        transition = self.transitions[i]
        if transition.max_path_length == 1:
            # Not splittable
            return []
        # It is splittable, but there are multiple ways to split.
        splits = []
        # First, what's the fewest number of nodes along this transition.  We don't care about 0 (no transition nodes,
        # so splits handled by shared nodes splits).  That is, 0 might be true, but then we don't need to do anything.
        # So set the lowest bid at 1.
        minimum_nodes = max(1, transition.min_path_length - 1)
        # Let's say that we are going to allow from 1 to 3 extra nodes.  We must have 1 node in common.
        # Then, if l is the number of (extra) nodes on the left path, and r is the number on the right,
        # we could have the following for (l,r):
        # Common node only: (0,0)           nodes_to_spread = 0
        # Common + 1: (1,0), (0,1)          nodes_to_spread = 1
        # Common + 2: (2,0), (1,1), (0,2)   nodes_to_spread = 2
        for nodes_to_spread in range(minimum_nodes - 1, transition.max_path_length - 1):
            for num_on_left in range(0, nodes_to_spread + 1):
                num_on_right = nodes_to_spread - num_on_left
                splits.append(self.split_at_transition(i, num_on_left, num_on_right))
        return splits

    def split_at_transition(self, transition_i, num_on_left, num_on_right):
        """Given a transition number and the exact number of nodes to include on each side, return a
        pair of one-sided query definitions"""
        # Generate_left_query and generate_right_query take a parameter (i) which is the index of the shared
        # nodes between the query.  Here we want to create queries that don't share a node.  That means that
        # the left query will have nodes 0 to i, and the right query will have i+1 to N.
        # We do want them to share a node, so we will add an untyped node and an extra transition.
        leftq = self.generate_left_query_def(transition_i)
        rightq = self.generate_right_query_def(transition_i + 1)
        leftq.transitions.append(Transition(leftq.node_types[-1], UNSPECIFIED, num_on_left - 1, num_on_left - 1))
        rightq.transitions.append(Transition(leftq.node_types[-1], UNSPECIFIED, num_on_right - 1, num_on_right - 1))
        leftq.node_types.append(UNSPECIFIED)
        rightq.node_types.append(UNSPECIFIED)
        return (leftq, rightq)

    def generate_paired_query_sharing_node(self, i):
        """From a Two Sided Query Definition, create two One Sided Query Definitions.
        The split is performed at the i-th node, which must occur in both sides."""
        if i == 0:
            return []
        return [(self.generate_left_query_def(i), self.generate_right_query_def(i))]


class UserQuery:
    """This is the class that the rest of builder uses to interact with a query."""

    def __init__(self, start_values, start_type, lookup_node):
        """Create an instance of UserQuery. Takes a starting value and the type of that value"""
        self.query = None
        self.definition = QueryDefinition()
        # Value for the original node
        self.definition.start_values = start_values
        self.definition.start_type = start_type
        self.definition.end_values = None
        # The term used to create the initial point
        self.definition.start_lookup_node = lookup_node
        # List of user-level types that we must pass through
        self.add_node(start_type)

    def add_node(self, node_type):
        """Add a node to the node list, validating the type
           20180108: node_type may be None"""
        # Our start node is more specific than this...  Need to have another validation method
        if node_type is not None and node_type not in node_types:
            raise Exception('node type must be one of greent.node_types')
        self.definition.node_types.append(node_type)

    def add_transition(self, next_type, min_path_length=1, max_path_length=1, end_values=None):
        """Add another required node type to the path.

        When a new node is added to the user query, the user is asserting that
        the returned path must go through a node of this type.  The default is
        that the next node should be directly related to the previous. That is,
        no other node types should be between the previous node and the current
        node.   There may be other nodes, but they will represent synonyms of
        the previous or current node.  This is defined using the
        max_path_length input, which defaults to 1.  On the other hand, a user
        may wish to define that some number of other node types must be between
        one node and another.  This can be specified by the min_path_length,
        which also defaults to 1.  If indirect edges are demanded, this
        parameter is set higher.  If this is the final transition, a value for
        the terminal node may be added.  Attempting to add more transitions
        after setting an end value will result in an exception.  If this is the
        terminal node, but it does not have a specified value, then no
        end_value needs to be specified.

        arguments: next_type: type of the output node from the transition.
                              Must be an element of reasoner.node_types.
                   min_path_length: The minimum number of non-synonym transitions
                                    to get from the previous node to the added node
                   max_path_length: The maximum number of non-synonym transitions to get
                                    from the previous node to the added node
                   end_value: Value of this node (if this is the terminal node, otherwise None)
        """
        # validate some inputs
        # TODO: subclass Exception
        if min_path_length > max_path_length:
            raise Exception('Maximum path length cannot be shorter than minimum path length')
        if self.definition.end_values is not None:
            raise Exception('Cannot add more transitions to a path with a terminal node')
        # Add the node to the type list
        self.add_node(next_type)
        # Add the transition
        t = Transition(self.definition.node_types[-2], next_type, min_path_length, max_path_length)
        self.definition.transitions.append(t)
        # Add the end_value
        if end_values is not None:
            self.definition.end_values = end_values

    def add_end_lookup_node(self, lookup_node):
        self.definition.end_lookup_node = lookup_node

    def compile_query(self, rosetta):
        """Based on the type of inputs that we have, create the appropriate form of query,
        and check that it can be satisfied by the typegraph"""
        if self.definition.end_values is None:
            # this is a one sided graph
            self.query = OneSidedLinearUserQuerySet(self.definition)
        else:
            # this is a two sided graph, we need to check every possible split point.
            # Temporarily, this does not include a single end-to-end path, but is always a pair of
            # one sided queries
            # TODO: REvisit
            # This approach of building two single sided queries works, but is somewhat flawed when we
            # allow for variable-length paths.   We end up splitting the one query into two queries and
            # optimizing each query, but that doesn't mean that we get the optimal solution.  Also, it means
            # that we need to do some extra checking that the node types are the same at either end of the
            # queries and handle synonyms between them.
            # It might be an improvement to let cypher handle all of this (if possible?) in one large query
            all_possible_query_defs = []
            for i in range(0, len(self.definition.transitions)):
                all_possible_query_defs += self.definition.generate_paired_query(i)
            # all_possible_query_defs = [self.definition.generate_paired_query(i) for i in
            #                           range(1, len(self.definition.transitions))]
            all_possible_queries = [TwoSidedLinearUserQuery(OneSidedLinearUserQuerySet(l),
                                                            OneSidedLinearUserQuerySet(r))
                                    for l, r in all_possible_query_defs]
            self.query = TwoSidedLinearUserQuerySet()
            for query in all_possible_queries:
                self.query.add_query(query, rosetta)
        return self.query.compile_query(rosetta)

    def get_terminal_types(self):
        return self.query.get_terminal_types()

    def generate_cypher(self):
        return self.query.generate_cypher()

    def get_start_node(self):
        return self.query.get_start_node()

    def get_reversed(self):
        return self.query.get_reversed()

    def get_lookups(self):
        return self.query.get_lookups()

    def get_neighbor_types(self, node_type):
        return self.query.get_neighbor_types(node_type)

class TwoSidedLinearUserQuerySet:
    """A composition of multiple two sided linear queries."""

    def __init__(self):
        self.queries = []

    def add_query(self, query, rosetta):
        if query.compile_query(rosetta):
            self.queries.append(query)

    def compile_query(self, rosetta):
        # by construction, we only accept queries that compile so don't re-check
        return len(self.queries) > 0

    def get_terminal_types(self):
        return sum([q.get_terminal_types() for q in self.queries], [])

    def generate_cypher(self):
        return sum([q.generate_cypher() for q in self.queries], [])

    def get_start_node(self):
        return sum([q.get_start_node() for q in self.queries], [])

    def get_reversed(self):
        return sum([q.get_reversed() for q in self.queries], [])

    def get_lookups(self):
        return sum([q.get_lookups() for q in self.queries], [])

    def get_neighbor_types(self, node_type):
        return_set = set()
        for q in self.queries:
            return_set.update( q.get_neighbor_types(node_type))
        return return_set




class TwoSidedLinearUserQuery:
    """Constructs a query that is fixed at either end.
    When this occurs, we are going to treat it as a pair of OneSidedLinearUserQueries that
    extend inward from the end points and meet in the middle"""

    def __init__(self, left_query, right_query):
        """To construct a two sided query, pass in two one-sided query"""
        # TODO: we want creation of this object to be a bit more dynamic
        # if left_query.node_types[-1] != right_query.node_types[-1]:
        #    raise ValueError('The left and right queries must end with the same node type')
        self.query1 = left_query
        self.query2 = right_query

    def get_terminal_types(self):
        return [self.query1.get_terminal_types()[0], self.query2.get_terminal_types()[0]]

    def generate_cypher(self):
        return self.query1.generate_cypher() + self.query2.generate_cypher()

    def get_start_node(self):
        return self.query1.get_start_node() + self.query2.get_start_node()

    def get_reversed(self):
        rleft = self.query1.get_reversed()
        rright = [True for r in self.query2.get_reversed()]
        return rleft + rright

    def get_lookups(self):
        return self.query1.get_lookups() + self.query2.get_lookups()

    def compile_query(self, rosetta):
        """Determine whether there is a path through the data that can satisfy this query"""
        individuals_ok = self.query1.compile_query(rosetta) and self.query2.compile_query(rosetta)
        if not individuals_ok:
            return False
        #Each side is traversable, but do they share a common endpoint?
        concepts_1 = self.query1.get_final_concepts()
        concepts_2 = self.query2.get_final_concepts()
        return len( concepts_1.intersection(concepts_2)) > 0

    def get_neighbor_types(self, node_type):
        neighbor_types = set()
        left = self.query1.get_neighbor_types(node_type)
        right =self.query2.get_neighbor_types(node_type)
        left_ends = set()
        right_ends = set()
        for pair in left:
            if None in pair:
                left_ends.add(pair)
            else:
                neighbor_types.add(pair)
        for pair in right:
            if None in pair:
                right_ends.add(pair)
            else:
                neighbor_types.add(pair)
        for le in left_ends:
            for re in right_ends:
                good_left = le[0]
                if good_left is None:
                    good_left = le[1]
                good_right = re[0]
                if good_right is None:
                    good_right = re[1]
                neighbor_types.add( (good_left, good_right) )
        return neighbor_types


class OneSidedLinearUserQuerySet:
    """A set of one-sided queries that will be run together.  Used to compose two sided queries"""

    def __init__(self, query_definition):
        self.lookup_node = query_definition.start_lookup_node
        self.queries = []
        for svalue in query_definition.start_values:
            self.queries.append(OneSidedLinearUserQuery(svalue, query_definition))

    def get_lookups(self):
        # This is just a way to get the lookup node for every query to be the same
        return [self.lookup_node for i in self.queries]

    def get_start_node(self):
        snodes = [q.get_start_node() for q in self.queries]
        return sum(snodes, [])

    def get_terminal_types(self):
        ttypes = [set(), set()]
        for q in self.queries:
            qt = q.get_terminal_types()
            for i in (0, 1):
                ttypes[i].update(qt[i])
        return ttypes

    def get_final_concepts(self):
        fc = set()
        for q in self.queries:
            fc.update(q.get_final_concepts())
        return fc

    def get_reversed(self):
        return [False for q in self.queries]

    def add_node(self, node_type):
        for q in self.queries:
            q.add_node(node_type)

    def add_transition(self, next_type, min_path_length=1, max_path_length=1, end_value=None):
        for q in self.queries:
            q.add_transition(next_type, min_path_length, max_path_length, end_value)

    def compile_query(self, rosetta):
        """Determine whether there is a path through the data that can satisfy this query"""
        # remove any queries that don't compile
        self.queries = list(filter(lambda q: q.compile_query(rosetta), self.queries))
        # is anything left?
        return len(self.queries) > 0

    def generate_cypher(self):
        cyphers = []
        for q in self.queries:
            cyphers += q.generate_cypher()
        return cyphers

    def get_neighbor_types(self, node_type):
        ntypes = set()
        for q in self.queries:
            ntypes.update(q.get_neighbor_types(node_type))
        return ntypes

class OneSidedLinearUserQuery:
    """A class for constructing linear paths through a series of knowledge sources.

    We have a set of knowledge sources that can be considered as a graph.  Each edge in the graph represents
    an endpoint in the sources (i.e. a service call) that takes (usually) one node and returns one or more nodes.
    These endpoints are typed, such as a service that takes drug ids and returns genes ids.

    To execute queries, we need to define a path through this graph, but the user should not be tasked with this.
    Instead, the user generates a high-level description of the kind of path that they want to execute, and
    it gets turned into a cypher query on the knowledge source graph.

    This class represents the user-level query"""

    def __init__(self, start_value, query_definition):
        """Create an instance of UserQuery. Takes a starting value and the type of that value"""
        self.start_value = start_value
        self.start_type = query_definition.start_type
        self.node_types = query_definition.node_types
        self.transitions = query_definition.transitions
        self.final_concepts=set()

    def get_start_node(self):
        node = self.node_types[0]
        if node in (DISEASE_NAME, DRUG_NAME):
            return [('{0}:{1}'.format(node, self.start_value), node)]
        return [(self.start_value, node)]

    def get_terminal_types(self):
        """Returns a two element array.  The first element is a set of starting terminal types.
        The second element is a set of ending terminal types"""
        print ("GET TERMINAL TYPES")
        print( ','.join( self.node_types ) )
        return [set([self.node_types[0]]), set([self.node_types[-1]])]

    def get_neighbor_types(self, query_node_type):
        neighbor_types = set()
        for node_number, node_type in enumerate(self.node_types):
            if node_number == 0:
                continue
            if node_type == query_node_type:
                if node_number == len(self.node_types) - 1:
                    pair =  (self.node_types[node_number-1], None)
                else:
                    pair =  (self.node_types[node_number-1], self.node_types[node_number+1])
                neighbor_types.add(pair)
        return neighbor_types

    @staticmethod
    def get_reversed():
        return [False]

    def get_final_concepts(self):
        return self.final_concepts

    def compile_query(self, rosetta):
        """Determine whether there is a path through the data that can satisfy this query"""
        cyphers = self.create_cypher(rosetta)
        if len(cyphers) == 0:
            return False
        programs = []
        for cypher in cyphers:
            programs += rosetta.type_graph.get_transitions(cypher)
        return len(programs) > 0

    def create_cypher(self,rosetta):
        cypher = self.generate_concept_cypher()
        #print(cypher)
        paths = rosetta.type_graph.run_cypher_query(cypher)
        if len(paths) == 0:
            return []
        concept_name_lists = [self.extract_concept_nodes(path) for path in paths.rows]
        self.cyphers = []
        for concept_names in concept_name_lists:
            self.final_concepts.add( concept_names[-1] )
            fullcypher = self.generate_type_cypher(concept_names)
            self.cyphers.append(fullcypher)
        return self.cyphers

    def generate_cypher(self):
        return self.cyphers

    def generate_type_cypher(self, concept_names):
        start_curie = Text.get_curie(self.start_value)
        buffer = f'MATCH p=(n0:Type)-[:SYNONYM*0..2]-(n0a:Type:{concept_names[0]})-[]->\n'
        for count, c_name in enumerate(concept_names[1:-1]):
            c = count + 1
            buffer += f'(n{c}:Type:{c_name})-[:SYNONYM*0..2]-(n{c}a:Type:{c_name})-[]->\n'
        c = len(concept_names) - 1
        #We add an extra synonym step at the end to help stitch together different arms of the query.
        buffer += f'(n{c}:Type:{concept_names[-1]})-[:SYNONYM*0..1]-(n{c}a:Type:{concept_names[-1]})\n'
        buffer += f'WHERE n0.name = "{start_curie}"\n'
        buffer += 'return p'
        return buffer

    @staticmethod
    def extract_concept_nodes(path):
        names = [segment[0]['name'] for segment in path]
        names.append(path[-1][-1]['name'])
        return names

    def generate_concept_cypher(self):
        """Generate a cypher query to find paths through the concept-level map."""
        cypherbuffer = ['MATCH\n']
        paths_parts = []
        for t_number, transition in enumerate(self.transitions):
            paths_parts.append(transition.generate_concept_cypher_pathstring(t_number))
        cypherbuffer.append(',\n'.join(paths_parts))
        cypherbuffer.append('\nWHERE\n')
        wheres = []
        for t_number, nodetype in enumerate(self.node_types):
            if nodetype != UNSPECIFIED:
                wheres.append(f'n{t_number}.name = "{nodetype}"')
        cypherbuffer.append('\nAND '.join(wheres))
        ps = [f'p{t}' for t in range(len(self.transitions))]
        cypherbuffer.append('\nRETURN ')
        cypherbuffer.append(','.join(ps))
        cypherbuffer.append('\n')
        return ''.join(cypherbuffer)

    def old_generate_cypher(self, end_value=None):
        """generate a cypher query to generate paths through the data sources. Optionally, callers can
        pass a specified end_value for the type-graph traversal."""
        cypherbuffer = ['MATCH']
        paths_parts = []
        for t_number, transition in enumerate(self.transitions):
            paths_parts.append(transition.generate_cypher_pathstring(t_number))
        cypherbuffer.append(',\n'.join(paths_parts))
        cypherbuffer.append('WHERE')
        curie_prefix = self.start_value.split(':')[0]
        cypherbuffer.append('n0.name="{}" AND'.format(curie_prefix))
        if end_value is not None:
            end_index = len(self.transitions)
            curie_prefix = end_value.split(':')[0]
            cypherbuffer.append('n{}.name="{}" AND'.format(end_index, curie_prefix))
        # NONE (r in relationships(p0) WHERE type(r) = "UNKNOWN"
        no_unknowns = []
        for t_number, transition in enumerate(self.transitions):
            no_unknowns.append('NONE (r in relationships(p{}) WHERE type(r) = "UNKNOWN")'.format(t_number))
        cypherbuffer.append('\nAND '.join(no_unknowns))
        # WITH (list of paths) (previous list of lengths) reduce () as dx, reduce() as synx WHERE [conditions on dx]
        with_parts = []
        pathstring = ','.join(['p{0}'.format(i) for i in range(len(self.transitions))])
        for t_number, transition in enumerate(self.transitions):
            with_parts.append(transition.generate_cypher_withstring(t_number, pathstring))
        cypherbuffer.append('\n'.join(with_parts))
        # SUM UP
        dvars = ['d{0}'.format(i) for i in range(len(self.transitions))]
        svars = ['Syn{0}'.format(i) for i in range(len(self.transitions))]
        dstring = ','.join(dvars)
        sstring = ','.join(svars)
        dsum = '+'.join(dvars)
        ssum = '+'.join(svars)
        sumup = '''WITH {0}, {1}, {2},\n {3} as TD,\n {4} as TS'''.format(pathstring, dstring, sstring, dsum, ssum)
        next_width = '''WITH {0}, TD, TS,'''.format(pathstring)
        getmin = ' min(TD) as minTD\n WHERE TD = minTD'
        rets = 'RETURN {0} ORDER BY TS ASC LIMIT 5'.format(pathstring)
        cypherbuffer.append(sumup)
        cypherbuffer.append(next_width)
        cypherbuffer.append(getmin)
        cypherbuffer.append(rets)
        return ['\n'.join(cypherbuffer)]


#########
#
#  Example Cypher queries.
#
#  These are the sorts of queries that we are trying to create with this class.
#
###

######
# New style
# First, we are removing "UNKNOWN" predicates, so we can drop that filtering
# Second, we are adding "translation" transitions between concepts to help find paths at the concept level

# So now, we can do this in two stages: Fill in unspecified nodes by querying the concept-level graph
# Then, fill in the actual transitions and synonyms going through the types returned from the concept-query.

# This is an example of how to build the path at the concept level. Here we are going from
# a name to a substance.  Then through 1 to 3 translation edges to a phenotype.  In this case 1 would 
# mean direct, 2 would mean a single intermediate node, and 3 would mean 2 intermediate nodes.
# We are finding shortest paths (so we prefer direct, but would take more) and we are finding all shortest,
# so if the shortest path is via one node, and there are two ways to do that then we get both.
# Finally we go from substance directly to phenotype.
# The return is a list of concept-paths that make explicit the hidden nodes in the shortest paths
# and check that we can actually do the traversal at the concept level.
new_q1 = '''
match 
p1=(nc:Concept)-[r3:translation]->(n:Concept),
p=allShortestPaths((n:Concept)-[r:translation*1..3]->(m:Concept)),
q=(m)-[r2:translation]->(:Concept) 
where nc.name = "Name" 
and n.name="Substance" 
and m.name="Phenotype" 
return p1,p,q
'''

# Once we have identified a concept path, we need to fill it in with actual type translations.
# Here, we have decided to go name->substance->gene->disease->phenotype.
# For each type translation, we first go between 0 and 1 synonyms to get to the id type that we want.
# 0 is included in case we dont need to do the transition, in wich case nodes e.g. n2a and n2 would be the same
# then we transition to the next type via an unspecified transition.
new_q2 = '''match p=
(n:Type:Name)-[r]->
(n2:Type:Substance)-[:SYNONYM*0..1]-(n2a:Type:Substance)-[]->
(n3:Type:Gene)-[:SYNONYM*0..1]-(n4:Type:Gene)-[]->
(n5:Type:Disease)-[:SYNONYM*0..1]-(n6:Type:Disease)-[]->
(n7:Type:Phenotype) 
return p'''
