# Copyright 2021 Xanadu Quantum Technologies Inc.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import numpy as np
from scipy.linalg import expm
from mrmustard.utils.types import *
from mrmustard.utils import graphics

#  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#  NOTE: the math backend is loaded automatically by the settings object
#  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


class Optimizer:
    r"""An optimizer for any parametrized object.
    It can optimize euclidean, orthogonal and symplectic parameters.

    NOTE: In the future it will also include a compiler, so that it will be possible to
    simplify the circuit/detector/gate/etc before the optimization and also
    compile other types of structures like error correcting codes and encoders/decoders.
    """

    def __init__(self, symplectic_lr: float = 0.1, orthogonal_lr: float = 0.1, euclidean_lr: float = 0.001):
        self.symplectic_lr: float = symplectic_lr
        self.orthogonal_lr: float = orthogonal_lr
        self.euclidean_lr: float = euclidean_lr
        self.loss_history: List[float] = [0]

    def minimize(self, cost_fn: Callable, by_optimizing: Sequence[Trainable], max_steps: int = 1000):
        r"""
        Minimizes the given cost function by optimizing circuits and/or detectors.
        Arguments:
            cost_fn (Callable): a function that will be executed in a differentiable context in order to compute gradients as needed
            by_optimizing (list of circuits and/or detectors and/or gates): a list of elements that contain the parameters to optimize
            max_steps (int): the minimization keeps going until the loss is stable or max_steps are reached (if `max_steps=0` it will only stop when the loss is stable)
        """
        params = {kind: extract_parameters(by_optimizing, kind) for kind in ("symplectic", "orthogonal", "euclidean")}
        bar = graphics.Progressbar(max_steps)
        with bar:
            while not self.should_stop(max_steps):
                loss, grads = loss_and_gradients(cost_fn, params)
                update_symplectic(params["symplectic"], grads["symplectic"], self.symplectic_lr)
                update_orthogonal(params["orthogonal"], grads["orthogonal"], self.orthogonal_lr)
                update_euclidean(params["euclidean"], grads["euclidean"], self.euclidean_lr)
                self.loss_history.append(loss)
                bar.step(numeric(loss))  # TODO

    def should_stop(self, max_steps: int) -> bool:
        r"""
        Returns True if the optimization should stop
        (either because the loss is stable or because the maximum number of steps is reached)
        """
        if max_steps != 0 and len(self.loss_history) > max_steps:
            return True
        if len(self.loss_history) > 20:  # if loss varies less than 10e-6 over 20 steps
            if sum(abs(self.loss_history[-i - 1] - self.loss_history[-i]) for i in range(1, 20)) < 1e-6:
                print("Loss looks stable, stopping here.")
                return True
        return False


# ~~~~~~~~~~~~~~~~~
# Static functions
# ~~~~~~~~~~~~~~~~~


def new_variable(value, bounds: Tuple[Optional[float], Optional[float]], name: str) -> Trainable:
    r"""
    Returns a new trainable variable from the current math backend
    with initial value set by `value` and bounds set by `bounds`.
    Arguments:
        value (float): The initial value of the variable
        bounds (Tuple[float, float]): The bounds of the variable
        name (str): The name of the variable
    Returns:
        variable (Trainable): The new variable
    """
    return math.new_variable(value, bounds, name)


def new_constant(value, name: str) -> Tensor:
    r"""
    Returns a new constant (non-trainable) tensor from the current math backend
    with initial value set by `value`.
    Arguments:
        value (numeric): The initial value of the tensor
        name (str): The name of the constant
    Returns:
        tensor (Tensor): The new constant tensor
    """
    return math.new_constant(value, name)


def new_symplectic(num_modes: int) -> Tensor:
    r"""
    Returns a new symplectic matrix from the current math backend
    with `num_modes` modes.
    Arguments:
        num_modes (int): The number of modes in the symplectic matrix
    Returns:
        tensor (Tensor): The new symplectic matrix
    """
    return math.random_symplectic(num_modes)


def new_orthogonal(num_modes: int) -> Tensor:
    return math.random_orthogonal(num_modes)


def numeric(tensor: Tensor) -> Tensor:
    return math.asnumpy(tensor)


def update_symplectic(symplectic_params: Sequence[Trainable], symplectic_grads: Sequence[Tensor], symplectic_lr: float):
    for S, dS_riemann in zip(symplectic_params, symplectic_grads):
        Y = math.riemann_to_symplectic(S, dS_riemann)
        YT = math.transpose(Y)
        new_value = math.matmul(S, math.expm(-symplectic_lr * YT) @ math.expm(-symplectic_lr * (Y - YT)))
        math.assign(S, new_value)


def update_orthogonal(orthogonal_params: Sequence[Trainable], orthogonal_grads: Sequence[Tensor], orthogonal_lr: float):
    for O, dO_riemann in zip(orthogonal_params, orthogonal_grads):
        D = 0.5 * (dO_riemann - math.matmul(math.matmul(O, math.transpose(dO_riemann)), O))
        new_value = math.matmul(O, math.expm(orthogonal_lr * math.matmul(math.transpose(D), O)))
        math.assign(O, new_value)


def update_euclidean(euclidean_params: Sequence[Trainable], euclidean_grads: Sequence[Tensor], euclidean_lr: float):
    math.euclidean_opt.lr = euclidean_lr
    math.euclidean_opt.apply_gradients(zip(euclidean_grads, euclidean_params))


def extract_parameters(items: Sequence, kind: str) -> List[Trainable]:
    r"""
    Extracts the parameters of the given kind from the given items.
    Arguments:
        items (Sequence[Trainable]): The items to extract the parameters from
        kind (str): The kind of parameters to extract. Can be "symplectic", "orthogonal", or "euclidean".
    Returns:
        parameters (List[Trainable]): The extracted parameters
    """
    params_dict = dict()
    for item in items:
        try:
            for p in item.trainable_parameters[kind]:
                if (hash := math.hash_tensor(p)) not in params_dict:
                    params_dict[hash] = p
        except TypeError:  # NOTE: make sure hash_tensor raises a TypeError when the tensor is not hashable
            continue
    return list(params_dict.values())


def loss_and_gradients(cost_fn: Callable, params: dict) -> Tuple[Tensor, Dict[str, Tensor]]:
    r"""
    Computes the loss and gradients of the cost function with respect to the parameters.
    The dictionary has three keys: "symplectic", "orthogonal", and "euclidean", to maintain
    the information of the different parameter types.

    Arguments:
        cost_fn (Callable): The cost function to be minimized
        params (dict): A dictionary of parameters to be optimized

    Returns:
        loss (float): The cost function of the current parameters
        gradients (dict): A dictionary of gradients of the cost function with respect to the parameters
    """
    loss, grads = math.loss_and_gradients(cost_fn, params)  # delegate entirely to backend
    return loss, grads