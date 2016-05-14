import numpy as np
import pandas as pd
import math
import os, re, logging, base64
from django.forms import model_to_dict

from whiskies.models import Whiskey, Tag, TagTracker
from elasticsearch import Elasticsearch


def euclidean_distance(v1, v2):
    squares = (v1 - v2) ** 2
    return math.sqrt(squares.sum())

tags = Tag.objects.all()


def get_tag_counts(whiskey, tags):
    # use all tagtrackers to create a tag:count dict.
    # Then loop through tag_list

    trackers = TagTracker.objects.filter(whiskey=whiskey).values()
    count_dict = {t["tag_id"]: t["count"] for t in trackers}
    counts = []
    for tag in tags:
        count = count_dict.get(tag.id, 0)
        counts.append(count)
    return np.array(counts)


def create_features_dict(whiskies, tags):
    # whiskies will be all whiskies of a certain type (ie. Scotch, Bourbon)

    # Return a dict of {whiskey_id: np.array([<tag counts>])}

    whisky_features = {}
    for whisky in whiskies:
        counts = get_tag_counts(whisky, tags)
        whisky_features[whisky.id] = counts
    return whisky_features


def create_scores(whiskey_ids, whiskey_features):

    results = []
    for column_whiskey in whiskey_ids:
        cell = {}

        for row_whiskey in whiskey_ids:
            if column_whiskey == row_whiskey:
                cell[row_whiskey] = np.nan
            else:
                distance = euclidean_distance(whiskey_features[column_whiskey],
                                              whiskey_features[row_whiskey])
                cell[row_whiskey] = distance
        results.append(cell)
    return results


def main_scores(whiskies, tags):
    # Create pandas dataframe distance matrix for all whiskies and tags
    # that are passed in.
    # Whiskies will usually be a filtered for a specific type.

    whiskey_features = create_features_dict(whiskies, tags)
    whiskey_ids = list(whiskey_features.keys())

    df = pd.DataFrame(create_scores(whiskey_ids, whiskey_features))

    df.index = whiskey_ids

    return df


def clear_saved(whiskey):

    for comp in whiskey.comparables.all():
        whiskey.comparables.remove(comp)


def update_whiskey_comps(whiskies, tags, number_comps=12):
    """
    score_df creates a matrix of Eulidean distances between all whiskies.
    """

    score_df = main_scores(whiskies, tags)

    for whiskey in whiskies:
        scores = score_df[whiskey.id].copy()
        scores.sort_values(inplace=True)

        clear_saved(whiskey)

        # Would like to store in reverse order they are added.
        for pk in scores.index[:number_comps]:
            whiskey.comparables.add(Whiskey.objects.get(pk=pk))


"""
elastic search functions
"""
import requests
import json


def prep_whiskey(whiskey):
    """
    Turn a whiskey object into a dict for indexing into elasticsearch
    """
    whiskey_dict = model_to_dict(whiskey)
    tags = []
    for track in whiskey.tagtracker_set.all():
        tags.append({
            "title": track.tag.title,
            "count": track.count
        })
    whiskey_dict["tags"] = tags
    return whiskey_dict


#  Probably have two different indices.
#  Set them up for heroku scheduling
def index_all_whiskies_local():
    """
    For indexing all whiskies locally
    """
    es = Elasticsearch([{'host': 'localhost', 'port': 9200}])
    for w in Whiskey.objects.all():
        es.index(index='whiskies', doc_type='whiskey', id=w.id,
                 body=model_to_dict(w))


def local_whiskey_search(searchstring):
    es = Elasticsearch([{'host': 'localhost', 'port': 9200}])
    #search_body = {"query": {"terms": {"title": searchstring}}}





    # Top level agg that works
    #
    # search_body = {
    #         "query": {
    #             "terms": {
    #                 "tags.title": searchstring
    #                     }
    #         },
    #         "aggs": {
    #             "total_price": {
    #                 "sum": {"field": "price"}
    #             },
    #             "speyside_type": {
    #                 "filter": {"term": {"region": "speyside"}},
    #                 "aggs": {
    #                     "avg_price": {"avg": {"field": "price"}},
    #                 }
    #             },
    #             "tag_count": {
    #                 "filter": {"terms": {"tags.title": searchstring}},
    #                 "aggs": {
    #                     "total_count": {"sum": {"field": "tags.count"}}
    #                 }
    #             }
    #         }
    #     }



    #Nested agg
    #https://www.elastic.co/guide/en/elasticsearch/guide/current/nested-aggregation.html
    #
    # search_body = {
    #     "query": {
    #         "terms": {
    #             "tags.title": searchstring
    #         },
    #         "aggs": {
    #             "tags": {
    #                 "nested": {
    #                     "path": "tags"
    #                 },
    #             },
    #             # "query": {
    #             #     "match": {}
    #             # },
    #             "aggs": {
    #                 "by_region": {
    #                     "region_histogram": {
    #                         "field": "region"
    #                     },
    #                     "aggs": {
    #                         "avg_count": {
    #                             "avg": {
    #                                 "field": "tags.count"
    #                             }
    #                         }
    #                     }
    #                 }
    #             }
    #         }
    #
    #         }
    #     }


    # Nested search

    # search_body = {
    #     "query": {
    #         "nested": {
    #             "path": "tags",
    #             "query": {
    #                 "match_all": {
    #                     "tags.title": searchstring
    #                 }
    #             }
    #         }
    #     }
    # }

    search_body = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"tags.title": "sweet"}},
                    {"term": {"tags.title": "amber"}},
                    {"term": {"tags.title": "rich"}}
                ]
            }
        }
    }

    return es.search(index="full_whiskies", body=search_body, size=400)


def index_all_whiskey_heroku():
    bonsai = os.environ['BONSAI_URL']

    auth = re.search('https\:\/\/(.*)\@', bonsai).group(1).split(':')
    host = bonsai.replace('https://%s:%s@' % (auth[0], auth[1]), '')

    es_header = [{
        'host': host,
        'port': 443,
        'use_ssl': True,
        'http_auth': (auth[0], auth[1])
    }]

    es = Elasticsearch(es_header)
    for w in Whiskey.objects.all():
        es.index(index='whiskies', doc_type='whiskey', id=w.id,
                 body=model_to_dict(w))


def heroku_search_whiskies(searchstring):
    """
    Main elasticsearch function.
    :param searchstring: A list of search terms, ie. ['term1', 'term2']
    """

    bonsai = os.environ['BONSAI_URL']

    auth = re.search('https\:\/\/(.*)\@', bonsai).group(1).split(':')
    host = bonsai.replace('https://%s:%s@' % (auth[0], auth[1]), '')

    es_header = [{
        'host': host,
        'port': 443,
        'use_ssl': True,
        'http_auth': (auth[0], auth[1])
    }]

    es = Elasticsearch(es_header)

    es.ping()

    search_body = {"query": {"terms": {"title": searchstring}}}
    return es.search(index="whiskies", body=search_body)


def custom_search(search_body):
    bonsai = os.environ['BONSAI_URL']

    auth = re.search('https\:\/\/(.*)\@', bonsai).group(1).split(':')
    host = bonsai.replace('https://%s:%s@' % (auth[0], auth[1]), '')

    es_header = [{
        'host': host,
        'port': 443,
        'use_ssl': True,
        'http_auth': (auth[0], auth[1])
    }]

    es = Elasticsearch(es_header)

    return es.search(index="whiskies", body=search_body)




"""
Functions for loading in data.
"""

#search_body = {"query":{"terms": {"title": ["aberlour", "10"]}}}
#search_body = { "terms": { "title": [ "aberlour"] }}
#body={"query": {"match" : { "title" : "Aberfeldy"}}}


mapping = {
    "tag_whiskey": {
        "properties": {

            "tags": {
                "type": "nested",
                "properties": {
                    "title": {"type": "string"},
                    "count": {"type": "short"}
                }
            }
        }
    }
}


"""
mapping = {
    "trip": {
        "properties": {
            "duration": {"type": "integer"},
            "start_date": {"type": "string"},
            "start_station": {"type": "string", "index": "not_analyzed"},
            "start_terminal": {"type": "integer"},
            "end_date": {"type": "string"},
            "end_station": {"type": "string", "index": "not_analyzed"},
            "end_terminal": {"type": "integer"},
            "bike_id": {"type": "string"},
            "subscriber": {"type": "string"}
        }
    }
}
"""
