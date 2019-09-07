import math
import numbers

from ..proba.base import ContinuousDistribution

from . import splitting


class Branch:

    def __init__(self, split, left, right, tree):
        self.split = split
        self.left = left
        self.right = right
        self.tree = tree

    @property
    def size(self):
        return self.left.size + self.right.size

    def get_leaf(self, x):
        if self.split(x):
            return self.left.get_leaf(x)
        return self.right.get_leaf(x)

    def update(self, x, y):
        if self.split(x):
            self.left = self.left.update(x, y)
            return self
        self.right = self.right.update(x, y)
        return self


class Leaf:

    __slots__ = 'depth', 'tree', 'target_dist', 'n_samples', 'split_enums', 'window'

    def __init__(self, depth, tree, target_dist):
        self.depth = depth
        self.tree = tree
        self.target_dist = target_dist
        self.n_samples = 0
        self.split_enums = {}

    @property
    def size(self):
        return 1

    @property
    def n_classes(self):
        """The number of observed classes."""
        if isinstance(self.target_dist, ContinuousDistribution):
            raise ValueError('The target is continuous, hence there are not classes')
        return len(self.target_dist)

    @property
    def is_pure(self):
        try:
            return self.n_classes < 2
        except ValueError:
            return False

    def get_leaf(self, x):
        return self

    @property
    def hoeffding_bound(self):
        """Returns the current Hoeffding bound.

        TODO: handle continuous target
        """
        R = math.log(self.n_classes)
        n = self.n_samples
        δ = self.tree.confidence
        return math.sqrt(R ** 2 * math.log2(1 / δ) / (2 * n))

    def update(self, x, y):

        # Update the class counts
        self.target_dist.update(y)
        self.n_samples += 1

        # Update the sufficient statistics of each feature's split searcher
        for i, xi in x.items():
            try:
                ss = self.split_enums[i]
            except KeyError:
                ss = self.split_enums[i] = (
                    splitting.HistSplitEnum(feature_name=i, n=30)
                    if isinstance(xi, numbers.Number) else
                    splitting.CategoricalSplitEnum(feature_name=i)
                )
            ss.update(xi, y)

        # Check if splitting is authorized or not
        if (
            self.depth >= self.tree.max_depth or
            self.is_pure or
            self.n_samples % self.tree.patience != 0
        ):
            return self

        # Search for the best split given the current information
        split, gain = self.find_best_split()

        # Calculate the Hoeffding bound
        ε = self.hoeffding_bound
        if gain > ε or ε < self.tree.tie_threshold:
            print(split)
            return Branch(
                split=split,
                left=Leaf(
                    depth=self.depth + 1,
                    tree=self.tree,
                    target_dist=self.target_dist.__class__().update(True).update(False)
                ),
                right=Leaf(
                    depth=self.depth + 1,
                    tree=self.tree,
                    target_dist=self.target_dist.__class__().update(True).update(False)
                ),
                tree=self.tree
            )
        return self

    def find_best_split(self):
        """Returns the best potential split."""

        current_impurity = self.tree.criterion(dist=self.target_dist)
        best_gain = -math.inf
        second_best_gain = -math.inf
        best_split = None

        # For each feature
        for ss in self.split_enums.values():

            # For each candidate split
            for split in ss.enumerate_splits(target_dist=self.target_dist, criterion=self.tree.criterion):

                # Determine the gain incurred by the split
                gain = current_impurity - split.impurity

                # Check if the gain brought by the candidate split is better than the current best
                if gain > best_gain:
                    best_gain, second_best_gain = gain, best_gain
                    best_split = split
                elif gain > second_best_gain:
                    second_best_gain = gain

        if best_split is None:
            raise RuntimeError('No best split was found')

        return best_split, best_gain - second_best_gain

    def predict(self, x):
        if isinstance(self.target_dist, ContinuousDistribution):
            return self.target_dist.mode
        return {c: self.target_dist.pmf(c) for c in self.target_dist}

    def predict_naive_bayes(self, x):
        """

        Example:

            >>> import itertools
            >>> from creme.tree.splitting import CategoricalSplitEnum

            >>> leaf = Leaf(0, None)

            >>> counts = [
            ...     ('A1', 'C1', 'A', 12),
            ...     ('A1', 'C1', 'B', 28),
            ...     ('A1', 'C2', 'A', 34),
            ...     ('A1', 'C2', 'B', 26),
            ...     ('A2', 'C1', 'C', 5),
            ...     ('A2', 'C1', 'D', 10),
            ...     ('A2', 'C1', 'E', 25),
            ...     ('A2', 'C2', 'C', 21),
            ...     ('A2', 'C2', 'D', 8),
            ...     ('A2', 'C2', 'E', 31),
            ...     ('A3', 'C1', 'F', 13),
            ...     ('A3', 'C1', 'G', 9),
            ...     ('A3', 'C1', 'H', 3),
            ...     ('A3', 'C1', 'I', 15),
            ...     ('A3', 'C2', 'F', 11),
            ...     ('A3', 'C2', 'G', 21),
            ...     ('A3', 'C2', 'H', 19),
            ...     ('A3', 'C2', 'I', 9)
            ... ]

            >>> for feature, feature_counts in itertools.groupby(counts, key=lambda x: x[0]):
            ...     leaf.split_enums[feature] = CategoricalSplitEnum()
            ...     for _, y, x, n in feature_counts:
            ...         for _ in range(n):
            ...             _ = leaf.split_enums[feature].update(x, y)

            >>> leaf.class_counts = {'C1': 40, 'C2': 60}

            >>> x = {'A1': 'B', 'A2': 'E', 'A3': 'I'}
            >>> leaf.predict(x)
            {'C1': 0.4, 'C2': 0.6}
            >>> leaf.predict_naive_bayes(x)
            {'C1': 0.7650830661614689, 'C2': 0.23491693383853113}

        """
        y_pred = self.predict(x)

        for i, xi in x.items():
            if i in self.split_enums:
                for label, dist in self.split_enums[i].items():
                    if isinstance(dist, ContinuousDistribution):
                        y_pred[label] *= dist.pdf(xi)
                    else:
                        y_pred[label] *= dist.pmf(xi)

        total = sum(y_pred.values())

        if total == 0:
            return {label: 1. / len(y_pred) for label in y_pred}

        for label, proba in y_pred.items():
            y_pred[label] /= total

        return y_pred
