import json
import logging

import urllib3
from elasticsearch import Elasticsearch
from functools     import reduce

from . factor import FactorBase

from ... environment import cfg

es_log = logging.getLogger("elasticsearch")
es_log.setLevel(logging.CRITICAL)

urllib3_log = logging.getLogger("urllib3")
urllib3_log.setLevel(logging.CRITICAL)

urllib3.disable_warnings()

class ElasticFactor(FactorBase):

    def __init__(self, url, size=500):
        """
        :param url: str
            Fully qualified url to an elasticsearch instance
        :param size: int
            Size limit to set on elasticsearch query
        """
        self.url  = url
        self.size = size
        self.es   = Elasticsearch([url],
                                  port=443,
                                  use_ssl=False,
                                  verify_certs=False,
                                  timeout=160,
        )

    def __repr__(self):
        return '{clsname}("{url}", size={size})'.format(
            clsname=self.__class__.__name__,
            url='<Elasticsearch URL>',
            size=self.size,
        )

    def flatten(self, nested):
        """
        Recursively flatten a nested list data structure

        :param nested: list
            Nested list

        :return: flattened
        :rtype:  list
        """
        return (
            [x for l in nested for x in self.flatten(l)]
                if isinstance(nested, list) else
            [nested]
        )

    def available(self, ad_id):
        """
        Get's the available factors for a particular ad

        :param ad_id: str
            Unique ad identifier

        :return: factors
        :rtype : list
        """
        accumulator = lambda x,y: x|y
        payload = {
                "size": self.size,
                "query": {
                    "match_phrase": {
                        "_id": ad_id
                    }
                }
            }
        results = self.es.search(body=payload)
        keys    = [set(i['_source'].keys()) for i in results['hits']['hits']]

        return list(reduce(accumulator, keys, set()))

    def initialize(self, ad_id, *factors):
        """
        :param ad_id: str
            Unique ad identifier
        :param factors: sequence
            Factors to merge on a particular ID

        :return: factors
        :rtype: dict
        """
        return {
            ad_id: {
                factor: dict(self.suggest(ad_id, factor)[ad_id][factor])
                    for factor in factors
            }
        }

    def get_all(self, ad_id):
        """
        :param ad_id: str
            Unique ad identifier

        :return: factors
        :rtype:  dict
        """
        return self.initialize(ad_id, *self.available(ad_id))

    def current_status(self, _id):
        """
        :param ad_id: str
            Unique ad identifier
        :param factors: sequence
            Factors to merge on a particular ID

        :return: factors
        :rtype: dict
        """
        payload = {
                    "query": {
                        "match": {
                            "_id": _id
                        }
                    }
                }
        es2 = Elasticsearch()
        res = es2.search(index="factor_state2016", body=payload)
        return res['hits']['hits'][0]


    def reduce(self, ad_id, *factors):
        """
        Combine factors together and reduce them to a set with the same `ad_ids`

        :param ad_id: str
            Unique ad identifier
        :param factors: sequence
            Factors to merge on a particular ID

        :return: reduced
        :rtype : set
        """
        union     = lambda x,y: x|y
        intersect = lambda x,y: x&y

        combined  = self.combine(ad_id, *factors)[ad_id]

        return reduce(intersect, (reduce(union,
                    map(set, combined[factor].values()), set()) for factor in factors))


    def lookup(self, ad_id, field):
        """
        Get data from ad_id

        :param ad_id: str
            String to be queried
        """
        payload = {
            "size": self.size,
            "query": {
                "ids": {
                    "values": [ad_id]
                }
            }
        }
        results = self.es.search(body=payload)

        return self.flatten([
            i['_source'][field] for i in results['hits']['hits']
                    if field in i['_source']
        ])

    def reverse_lookup(self, field, field_value):
        """
        Get ad_id from a specific field and search term

        :param field_value: str
            String to be queried
        """

        payload = {
                "size": self.size,
                "query": {
                    "match_phrase": {
                        field: field_value
                    }
                }
            }
        results = self.es.search(body=payload)

        # If the Elastic Search returns 0 results,
        # then change the search field from `self.field` to all and search again.
        if not results['hits']['total']:
            payload = {
                "size": self.size,
                "query": {
                    "match_phrase": {
                        "_all": field_value
                    }
                }
            }
            results = self.es.search(body=payload)

        return [hit['_id'] for hit in results['hits']['hits']]

    def merge(self, a, b):
        """
        Get intersection of two factor states
        : param a: dict
            dict to merge with another dict
        : param factor_state_b: dict
            dict to merge with another dict

        : rtype: dict
            returns a dictionary with the intersecting factors, factors present in a not b and vice versa, and the network representation of the factors so analysts can back track the order
        """
        factors = {}
        network = {}
        everything = {}

        data_a = self.populater(a)
        data_b = self.populater(b)

        factors_a = data_a["factors"]
        factors_b = data_b["factors"]

        factors["intersection"] = factors_a.intersection(factors_b)
        factors["workflow_a"] = factors_a.difference(factors_b)
        factors["workflow_b"] = factors_b.difference(factors_a)

        network["intersection"] = self.check(factors["intersection"], data_a)
        network["workflow_a"] = self.check(factors["workflow_a"], data_a)
        network["workflow_b"] = self.check(factors["workflow_b"], data_b)

        everything["factors"] = factors
        everything["network"] = network
        return(everything)

    def check(self, factors, data):
        """
        Get relationships of relevant factors
        : param factors: dict
            dict to merge with another dict
        : param data: dict
            dict to merge with another dict

        : rtype: dict
            returns a dictionary with the the relationships for the specified factors
        """
        network = []
        for k in factors:
            if k in data["relationships"]:
                network.list(data["relationships"])
        return network

    def populater(self, dictionary):
        """
        Get intersection of two factor states
        : param a: dict
            dict to intersect with another dict
        : param factor_state_b: dict
            dict to intersect with another dict

        : rtype: dict
        Return: set of factor types/values, dictionary containing the network structure of the original dict
        """
        data = {}
        factors = set()
        relationships = {}
        for k1, v1 in dictionary.items():
            if "sourcecontents" in k1:
                for k2, v2 in v1.items():
                    if isinstance(v2, dict):
                        for k3, v3 in v2.items():
                            if isinstance(v3, dict):
                                for k4, v4 in v3.items():
                                    if k1 == "sourcecontents_1":
                                        factors.add(k3 + "_" + k4)
                                        relationships[k2] = k3 + "_" + k4
                                    elif isinstance(v4, dict):
                                        for k5 in v4.items():
                                            factors.add(k4 + "_" + k5)
                                            relationships[k3] = k4 + "_" + k5
        data["factors"] = factors
        data["relationships"] = relationships
        return data

    # def special_lookup(self, field, field_value):
    #     """
    #     Get ad_id from a specific field and search term

    #     :param field_value: str
    #         String to be queried
    #     """

    #     payload = {
    #             "size": self.size,
    #             "query": {
    #                 "match_phrase": {
    #                     field: field_value
    #                 }
    #             }
    #         }
    #     results = self.es.search(body=payload)

    #     # If the Elastic Search returns 0 results,
    #     # then change the search field from `self.field` to all and search again.
    #     if not results['hits']['total']:
    #         payload = {
    #             "size": self.size,
    #             "query": {
    #                 "match_phrase": {
    #                     "_all": field_value
    #                 }
    #             }
    #         }
    #         results = self.es.search(body=payload)
    #     a = set()
    #     result_tuple = {}
    #     for hit in results['hits']['hits']:
    #         if hit["_source"][field] != field_value:
    #             result_tuple[(field, hit["_source"][field])] =  hit["_id"]
    #             a.add(result_tuple)
    #             result_tuple = {}
    #     for i in result_tuple:
    #         if i[2]
    #     return new_field_values


def combine_two_factors(original: dict, addition: dict) -> dict:
    """
    Union two dictionaries

    :param original: dict
        Dict to union
    :param addition: dict
        Dict to union
    """
    for k1 in addition:
        if k1 not in original:
            original[k1] = {}
        for k2 in addition[k1]:
            original[k1][k2] = addition[k1][k2]

    return original


def combine_multi_factors(factors: list) -> dict:
    """
    Union multiple dictionaries

    :param factors: list of dicts
        List of dicts to union
    """
    original = factors[0]
    for i in factors[1:]:
        if i:
            original = combine_two_factors(original, i)

    return original


def suggest(ad_id: str, factor_label: str, url: str):
    """
    :param ad_id: str
        Str of number
    :param factor_label: str
        Str to specify factor
    :param url: str
        Str for Elastic Search instance
    """
    try:
        factor = ElasticFactor(url)
        suggestions = factor.suggest(ad_id, factor_label)
        # print(factor_type, len(suggestions[ad_id][factor_type]))
        if len(suggestions[ad_id][factor_label]) == 0:
            return None
        else:
            return suggestions
    except TypeError:
        # print(factor_type, 0)
        return None


def factor_constructor(ad_id: str, factor_labe3: list, url: str) -> dict:
    """
    :param ad_id: str
        Str of number
    :param factor_labels: list of strings
        List of strings to specify factors
    :param url: str
        Str for Elastic Search instance
    """
    suggestions = [
        suggest(ad_id, i, url) for i in factor_labels
    ]

    return combine_multi_factors(suggestions)


def flatten(data: dict, level: int) -> set:
    """
    :param data: dict
        Dict of suggestions generated by the factor_constructor
    :param level: int
        Int for the maximum levels of recursion for flattening
    """
    results = set()

    def get_ad_ids(subdict, results, depth):
        """
        :param subdict: dict
            Dict that gets iteratively smaller
        :param results: set
            Set containing the results
        :param depth: int
            Int specifying the current recursion level
        """
        if level <= depth:
            for k, v in subdict.items():
                for i in v:
                    results.add(i)
        else:
            for k, v in subdict.items():
                if isinstance(v, dict):
                    depth = depth + 1
                    get_ad_ids(v, results, depth)
                else:
                    for i in v:
                        results.add(i)

    get_ad_ids(data, results, 0)
    return results


def prune(data, keepers):
    #Remove everything that doesn't contain the keepers
    # def visit(subdict):
    #     keep = []
    #     for k, v in subdict.items():
    #         for i in keepers:
    #             if i in v:
    #                 print(k[v])
    #                 keep.append(k[v])
    #             elif isinstance(v, dict):
    #                 visit(v)
    #             else:
    #                 pass
    #     return keep
    # keep = visit(data)
    # copy = data
    # for k1 in copy:
    #     for k2 in copy[k1]:
    #         if k2 not in keep:
    #             del data[k1][k2]
    #         try:
    #             for k3 in copy[k1][k2]:
    #                 if k3 not in keep.values():
    #                     del data[k1][k2][k3]
    #         except KeyError:
    #             pass
    return {}


def extend(data: dict, url: str, factor_values: list, degree: str) -> dict:
    """
    :param data: dict
        Dict of suggestions generated by the factor_constructor
    :param url: str
        Str for Elastic Search instance
    :param factor_values: List of strings
        List of strings to specify factors
    :param degree: Str
        String to specify the extension name
    """
    field_values = set()
    for v in data.values():
        for value in flatten(v, 1):
            field_values.add(value)
    data[degree] = {}
    ad_ids_2 = flatten(data, 10)
    for ad_id in ad_ids_2:
        addition = factor_constructor(ad_id,  factor_values, url)
        print(".")
        new_field_values = list(flatten(addition, 1))
        for value in new_field_values:
            if value in field_values:
                new_field_values.remove(value)
        print("new fields:", new_field_values)
        data[degree] = prune(addition, new_field_values)
    return data


def main():
    url = cfg["cdr_elastic_search"]["hosts"] + cfg["cdr_elastic_search"]["index"]
    ad_id = "63166071"
    # ad_id = "71046685"
    # ad_id = "31318107"
    # ad_id2 = "62442471"
    """
    Here's an example using the factors and the factor constructor
    """
    x = {}
    x["original"] = factor_constructor(ad_id, ["phone", "email", "text", "title"], url)
    print(json.dumps(x))

    # print(json.dumps(x))
    # print(flatten(x, 2))
    b = extend(x, url, ["phone", "email", "text", "title"], "ext2")
    print(json.dumps(b))
    # c = extend(b, url, "all", ["phone"], "ext3")
    # print(json.dumps(c))
