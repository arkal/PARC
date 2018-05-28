"""
Minimum Spanning Tree Clustering
"""
from __future__ import division

import numpy as np
import sys

from scipy import sparse
from scipy.sparse.csgraph import minimum_spanning_tree, connected_components
from scipy.sparse.csgraph._validation import validate_graph
from sklearn.utils import check_array
import time

from sklearn.base import BaseEstimator, ClusterMixin
from sklearn.neighbors import kneighbors_graph
from sklearn.metrics import pairwise_distances
from sklearn.neighbors import KNeighborsClassifier
from scipy.spatial import distance


class MSTClustering(BaseEstimator, ClusterMixin):
    """Minimum Spanning Tree Clustering

    Parameters
    ----------
    cutoff : float, int, optional
        either the number of edges to cut (if cutoff >= 1) or the fraction of
        edges to cut (if 0 < cutoff < 1). See also the ``cutoff_scale``
        parameter.
    cutoff_scale : float, optional
        minimum size of edges. All edges larger than cutoff_scale will be
        removed (see also ``cutoff`` parameter).
    min_cluster_size : int (default: 1)
        minimum number of points per cluster. Points belonging to smaller
        clusters will be assigned to the background.
    approximate : bool, optional (default: True)
        If True, then compute the approximate minimum spanning tree using
        n_neighbors nearest neighbors. If False, then compute the full
        O[N^2] edges (see Notes, below).
    n_neighbors : int, optional (default: 20)
        maximum number of neighbors of each point used for approximate
        Euclidean minimum spanning tree (MST) algorithm.  Referenced only
        if ``approximate`` is False. See Notes below.
    metric : string (default "euclidean")
        Distance metric to use in computing distances. If "precomputed", then
        input is a [n_samples, n_samples] matrix of pairwise distances (either
        sparse, or dense with NaN/inf indicating missing edges)
    metric_params : dict or None (optional)
        dictionary of parameters passed to the metric. See documentation of
        sklearn.neighbors.NearestNeighbors for details.

    Attributes
    ----------
    full_tree_ : sparse array, shape (n_samples, n_samples)
        Full minimum spanning tree over the fit data
    T_trunc_ : sparse array, shape (n_samples, n_samples)
        Non-connected graph over the final clusters
    labels_: array, length n_samples
        Labels of each point

    Notes
    -----
    This routine uses an approximate Euclidean minimum spanning tree (MST)
    to perform hierarchical clustering.  A true Euclidean minimum spanning
    tree naively costs O[N^3].  Graph traversal algorithms only help so much,
    because all N^2 edges must be used as candidates.  In this approximate
    algorithm, we use k << N edges from each point, so that the cost is only
    O[Nk log(Nk)]. For k = N, the approximation is exact; in practice for
    well-behaved data sets, the result is exact for k << N.
    """

    def __init__(self, cutoff=None, cutoff_scale=None, min_cluster_size=1,
                 approximate=True, n_neighbors=20,
                 metric='euclidean', metric_params=None, sigma_factor=1):
        # self.cutoff = cutoff
        self.cutoff_scale = cutoff_scale
        self.min_cluster_size = min_cluster_size
        self.approximate = approximate
        self.n_neighbors = n_neighbors
        self.metric = metric
        self.metric_params = metric_params
        self.sigma_factor = sigma_factor

    def too_close(self, X_dict_mean, X_dict_label, labels, mean):
        updated = False
        for key1 in X_dict_mean.keys():
            # print('key1 is ', key1)
            for key2 in X_dict_mean.keys():
                (x1, y1) = X_dict_mean[key1][0]
                (x2, y2) = X_dict_mean[key2][0]
                coords = [(x1, y1), (x2, y2)]
                dist = distance.cdist(coords, coords, 'euclidean')[0, 1]
                if (dist > 0) and (dist < 10 * mean):
                    idx = np.where(labels == key2)[0]
                    labels[idx] = key1
                    # print(X_dict_label[key1])
                    # print(X_dict_label[key2])
                    X_dict_label[key1].extend(list(X_dict_label[key2]))
                    # print(X_dict_label[key1])
                    X_dict_label.pop(key2, None)
                    x = [t[0] for t in X_dict_label[key1]]
                    y = [t[1] for t in X_dict_label[key1]]
                    X_dict_mean[key1] = [(np.mean(x), np.mean(y))]
                    X_dict_mean.pop(key2, None)
                    # print('key',key1, 'was too close to key', key2)
                    updated = True
                    return X_dict_mean, X_dict_label, labels, updated
        updated = False
        return X_dict_mean, X_dict_label, labels, updated

    def fit(self, X, y=None):
        """Fit the clustering model

        Parameters
        ----------
        X : array_like
            the data to be clustered: shape = [n_samples, n_features]
        """
        time_start = time.time()
        # if self.cutoff is None and self.cutoff_scale is None:
        if self.cutoff_scale is None:
            raise ValueError("Must specify either cutoff or cutoff_frac")

        # Compute the distance-based graph G from the points in X
        if self.metric == 'precomputed':
            # Input is already a graph. Copy if sparse
            # so we can overwrite for efficiency below.
            self.X_fit_ = None
            G = validate_graph(X, directed=True,
                               csr_output=True, dense_output=False,
                               copy_if_sparse=True, null_value_in=np.inf)
        elif not self.approximate:
            X = check_array(X)
            self.X_fit_ = X
            kwds = self.metric_params or {}
            G = pairwise_distances(X, metric=self.metric, **kwds)
            G = validate_graph(G, directed=True,
                               csr_output=True, dense_output=False,
                               copy_if_sparse=True, null_value_in=np.inf)
        else:
            # generate a sparse graph using n_neighbors of each point
            X = check_array(X)
            self.X_fit_ = X
            n_neighbors = min(self.n_neighbors, X.shape[0] - 1)
            G = kneighbors_graph(X, n_neighbors=n_neighbors,
                                 mode='distance',
                                 metric=self.metric,
                                 metric_params=self.metric_params)

        # HACK to keep explicit zeros (minimum spanning tree removes them)
        zero_fillin = G.data[G.data > 0].min() * 1E-8
        G.data[G.data == 0] = zero_fillin

        # Compute the minimum spanning tree of this graph
        self.full_tree_ = minimum_spanning_tree(G, overwrite=True)

        # undo the hack to bring back explicit zeros
        self.full_tree_[self.full_tree_ == zero_fillin] = 0

        # Partition the data by the cutoff
        N = G.shape[0] - 1
        # if self.cutoff is None:
        # i_cut = N
        # elif 0 <= self.cutoff < 1:
        # i_cut = int((1 - self.cutoff) * N)
        # elif self.cutoff >= 1:
        # i_cut = int(N - self.cutoff)
        # else:
        # raise ValueError('self.cutoff must be positive, not {0}'
        # ''.format(self.cutoff))

        # create the mask; we zero-out values where the mask is True
        N = len(self.full_tree_.data)
        mask = np.zeros(N, dtype=bool)  # mask is not true anywhere
        # if i_cut < 0:
        # mask = np.ones(N, dtype=bool)
        # elif i_cut >= N:
        # elif i_cut >= 0:
        # mask = np.zeros(N, dtype=bool)
        # print('not cutting any edges specified by icut param')
        # else:
        # mask = np.ones(N, dtype=bool)
        # part = np.argpartition(self.full_tree_.data, i_cut)
        # mask[part[:i_cut]] = False

        # additionally cut values above the ``cutoff_scale``
        if self.cutoff_scale is not None:
            d = self.full_tree_.data
            mu = np.mean(d)
            sigma = np.std(d)
            print('mean edge length: ', mu, 'std edge length: ', sigma)
            mask |= (self.full_tree_.data > (mu + self.sigma_factor * sigma))
            # mask |= (self.full_tree_.data > self.cutoff_scale)

        # Trim the tree
        cluster_graph = self.full_tree_.copy()

        # Eliminate zeros from cluster_graph for efficiency.
        # We want to do this:
        #    cluster_graph.data[mask] = 0
        #    cluster_graph.eliminate_zeros()
        # but there could be explicit zeros in our data!
        # So we call eliminate_zeros() with a stand-in data array,
        # then replace the data when we're finished.
        original_data = cluster_graph.data
        cluster_graph.data = np.arange(1, len(cluster_graph.data) + 1)
        cluster_graph.data[mask] = 0
        cluster_graph.eliminate_zeros()
        cluster_graph.data = original_data[cluster_graph.data.astype(int) - 1]

        # find connected components
        n_components, labels = connected_components(cluster_graph,
                                                    directed=False)
        print('num connected components:', n_components)
        # print('labels:', labels )

        # remove clusters with fewer than min_cluster_size
        counts = np.bincount(labels)
        to_remove = np.where(counts < self.min_cluster_size)[0]

        if len(to_remove) > 0:
            for i in to_remove:
                labels[labels == i] = -1
            dummy, labels = np.unique(labels, return_inverse=True)
            labels -= 1  # keep -1 labels the same
            # print('values of the labels after removing small clusters: ',dummy)
            # print('index of the label array (above), which we use as the new labels', labels)
        # update cluster_graph by eliminating non-clusters
        # operationally, this means zeroing-out rows & columns where
        # the label is negative.
        I = sparse.eye(len(labels))
        I.data[0, labels < 0] = 0

        # we could just do this:
        #   cluster_graph = I * cluster_graph * I
        # but we want to be able to eliminate the zeros, so we use
        # the same indexing trick as above
        original_data = cluster_graph.data
        cluster_graph.data = np.arange(1, len(cluster_graph.data) + 1)
        cluster_graph = I * cluster_graph * I
        cluster_graph.eliminate_zeros()
        cluster_graph.data = original_data[cluster_graph.data.astype(int) - 1]

        X_big = X[labels != -1]
        # print('shape of X_big ',X_big.shape)
        X_small = X[labels == -1]
        print('x_small shape: ', X_small.shape)
        if X_small.shape[0] > 0:
            labels_big = labels[labels != -1]
            neigh = KNeighborsClassifier(n_neighbors=3)
            neigh.fit(X_big, labels_big)
            print('made KneighborClassifier')
            y_small = neigh.predict(X_small)
            # print('y_small shape:',y_small.shape)
            y_small_ix = np.where(labels == -1)[0]
            print('made outlier labels of length', len(y_small_ix))
            ii = 0
            for iy in y_small_ix:
                labels[iy] = y_small[ii]
                # print(y_small[ii])
                ii = ii + 1

        labels_knn_outlier = list(labels)
        X_dict = {}
        X_dict_mean = {}
        X_dict_population = {}
        mean_array = np.empty([n_components, 2])
        Ntot = len(labels)

        for dd in range(len(labels)):
            x = X[dd, 0]
            y = X[dd, 1]
            X_dict.setdefault(labels[dd], []).append((x, y))

        num_big_components = len(X_dict)
        print('num_big_components', num_big_components)
        for key in range(num_big_components):
            # print('key',key)
            x = [t[0] for t in X_dict[key]]
            y = [t[1] for t in X_dict[key]]
            mean_array[key, 0] = np.mean(x)
            mean_array[key, 1] = np.mean(y)
            X_dict_mean.setdefault(key, []).append((np.mean(x), np.mean(y)))
            #X_dict_population.setdefault(key, []).append(counts[key])
        # print('dict of big cluster mean location', X_dict_mean)
        updated = True
        while updated == True:
            X_dict_mean, X_dict, labels, updated = self.too_close(X_dict_mean, X_dict, labels, mu)
        clustering_runtime = time.time() - time_start
        self.labels_ = labels
        self.labels_nomerge_ = labels_knn_outlier
        self.cluster_graph_ = cluster_graph
        self.clustering_runtime_ = clustering_runtime
        return self

    def get_graph_segments(self, full_graph=False):
        """Convenience routine to get graph segments

        This is useful for visualization of the graph underlying the algorithm.

        Parameters
        ----------
        full_graph : bool (default: False)
            If True, return the full graph of connections. Otherwise return
            the truncated graph representing clusters.

        Returns
        -------
        segments : tuple of ndarrays
            the coordinates representing the graph. The tuple is of length
            n_features, and each array is of size (n_features, n_edges).
            For n_features=2, the graph can be visualized in matplotlib with,
            e.g. ``plt.plot(segments[0], segments[1], '-k')``
        """
        if not hasattr(self, 'X_fit_'):
            raise ValueError("Must call fit() before get_graph_segments()")
        if self.metric == 'precomputed':
            raise ValueError("Cannot use ``get_graph_segments`` "
                             "with precomputed metric.")

        n_samples, n_features = self.X_fit_.shape

        if full_graph:
            G = sparse.coo_matrix(self.full_tree_)
        else:
            G = sparse.coo_matrix(self.cluster_graph_)
        labels = self.labels_
        n_labels = len(set(labels))
        X_mean = np.empty([n_features, 2])
        labels_mean = np.empty([n_labels, 2])
        keep_segment_row_x = []
        keep_segment_col_x = []
        keep_segment_row_y = []
        keep_segment_col_y = []
        xpairs = []
        ypairs = []
        xpairs_mean = []
        ypairs_mean = []

        for key in set(labels):
            idx = np.where(labels == key)[0].tolist()
            # print('idx',idx)
            x = self.X_fit_[idx, 0]
            y = self.X_fit_[idx, 1]
            # print('y',y)
            labels_mean[key, 0] = np.mean(x)
            labels_mean[key, 1] = np.mean(y)

        # print('mean labels', labels_mean)
        # for i in n_samples:
        # X_mean[i,0] = label_means[label[i],0]
        # X_mean[i,1] = label_means[label[i],1]

        shortlist = [i for i in range(len(G.row)) if labels[G.row[i]] != labels[G.col[i]]]
        print('shortlist', shortlist)
        for k in shortlist:
            group_pair = (labels[G.row[k]], labels[G.col[k]])
            # print('group pair', group_pair)
            xends = [self.X_fit_[G.row[k]][0], self.X_fit_[G.col[k]][0]]
            yends = [self.X_fit_[G.row[k]][1], self.X_fit_[G.col[k]][1]]
            xpairs.append(xends)
            ypairs.append(yends)
            xends_mean = [labels_mean[labels[G.row[k]]][0], labels_mean[labels[G.col[k]]][0]]
            # print('xends mean', xends_mean)
            yends_mean = [labels_mean[labels[G.row[k]]][1], labels_mean[labels[G.col[k]]][1]]
            # print('yends mean', yends_mean)
            xpairs_mean.append(xends_mean)
            ypairs_mean.append(yends_mean)
            # print('ypairs_mean',ypairs_mean)
        xlist = []
        ylist = []
        xlist_mean = []
        ylist_mean = []
        for xends, yends in zip(xpairs, ypairs):
            xlist.extend(xends)
            xlist.append(None)
            ylist.extend(yends)
            ylist.append(None)
        # print('xpairs', xpairs_mean)
        # print('ypairs', ypairs_mean)
        for xends_mean, yends_mean in zip(xpairs_mean, ypairs_mean):
            xlist_mean.extend(xends_mean)
            xlist_mean.append(None)
            ylist_mean.extend(yends_mean)
            ylist_mean.append(None)
        new = tuple((xlist, ylist))
        new_mean = tuple((xlist_mean, ylist_mean))
        original = tuple(np.vstack(arrs) for arrs in zip(self.X_fit_[G.row].T, self.X_fit_[G.col].T))
        # print('new mean', new_mean)
        return new_mean