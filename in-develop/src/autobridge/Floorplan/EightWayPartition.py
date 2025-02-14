import logging
import autobridge.Floorplan.Utilities as util

from typing import Dict, List, Callable, Optional, Tuple
from mip import Model, Var, minimize, xsum, BINARY, INTEGER, OptimizationStatus
from itertools import product
from autobridge.Opt.DataflowGraph import Vertex
from autobridge.Opt.Slot import Slot
from autobridge.Opt.SlotManager import SlotManager, Dir

_logger = logging.getLogger().getChild(__name__)


def eight_way_partition(
  init_v2s: Dict[Vertex, Slot],
  slot_manager: SlotManager,
  grouping_constraints: List[List[Vertex]],
  pre_assignments: Dict[Vertex, Slot],
  ref_usage_ratio: float,
  max_search_time: int = 600,
  warm_start_assignments: Dict[Vertex, Slot] = {},
  max_usage_ratio_delta: float = 0.02
) -> Dict[Vertex, Slot]:
  """
  adjust the max_usage_ratio if failed
  """
  curr_max_usage = ref_usage_ratio
  while 1:
    v2s = _eight_way_partition(init_v2s, grouping_constraints, pre_assignments, slot_manager, curr_max_usage, max_search_time, warm_start_assignments)
    if not v2s:
      _logger.info(f'eight way partition failed with max_usage_ratio {curr_max_usage}')
      curr_max_usage += max_usage_ratio_delta
    else:
      break

  _logger.info(f'eight way partition succeeded with max_usage_ratio {curr_max_usage}')
  return v2s


def _eight_way_partition(
  init_v2s: Dict[Vertex, Slot],
  grouping_constraints: List[List[Vertex]],
  pre_assignments: Dict[Vertex, Slot],
  slot_manager: SlotManager,
  max_usage_ratio: float,
  max_search_time: int,
  warm_start_assignments: Dict[Vertex, Slot],
) -> Dict[Vertex, Slot]:

  m = Model()
  if not _logger.isEnabledFor(logging.DEBUG):
    m.verbose = 0

  v_list = list(init_v2s.keys())

  # three variables could determine the location of a module
  # y = y1 *2 + y2  (four slots)
  # x = x           (each SLR is divided by half)
  v2var_x, v2var_y1, v2var_y2 = dict(), dict(), dict()
  for v in v_list:
    v2var_x[v] = m.add_var(var_type=BINARY, name=f'{v.name}_x')
    v2var_y1[v] = m.add_var(var_type=BINARY, name=f'{v.name}_y1')
    v2var_y2[v] = m.add_var(var_type=BINARY, name=f'{v.name}_y2')

  func_get_slot_by_idx = _get_slot_by_idx_closure(slot_manager)
  slot_to_idx = _get_slot_to_idx(func_get_slot_by_idx)

  _add_area_constraints(m, v_list, v2var_x=v2var_x, v2var_y1=v2var_y1, v2var_y2=v2var_y2,
    func_get_slot_by_idx=func_get_slot_by_idx, max_usage_ratio=max_usage_ratio)

  _add_pre_assignment(m, v_list, slot_to_idx, pre_assignments, v2var_x=v2var_x, v2var_y1=v2var_y1, v2var_y2=v2var_y2)

  _add_grouping_constraints(m, grouping_constraints, v2var_x=v2var_x, v2var_y1=v2var_y1, v2var_y2=v2var_y2)

  _add_opt_goal(m, v_list, v2var_x=v2var_x, v2var_y1=v2var_y1, v2var_y2=v2var_y2)

  if warm_start_assignments:
    _add_warm_start_assignment(m, v_list, slot_to_idx, warm_start_assignments, v2var_x=v2var_x, v2var_y1=v2var_y1, v2var_y2=v2var_y2)

  _logger.info(f'Start ILP solver with max usage ratio {max_usage_ratio} and max search time {max_search_time}s')
  m.optimize(max_seconds=max_search_time)

  next_v2s = _get_results(m, v_list, func_get_slot_by_idx, v2var_x=v2var_x, v2var_y1=v2var_y1, v2var_y2=v2var_y2)

  return next_v2s


def _get_slot_by_idx_closure(
  slot_manager: SlotManager
) -> Callable[[int, int, int], Slot]:
  # slot_group = [slot_000, slot_001, \
  #               slot_010, slot_011, \
  #               slot_100, slot_101, \
  #               slot_110, slot_111 ]

  # must not change order!
  partition_order = [Dir.horizontal, Dir.horizontal, Dir.vertical]
  all_leaf_slots = slot_manager.getLeafSlotsAfterPartition(partition_order)

  def func_get_slot_by_idx(y1, y2, x):
    idx = y1 * 4 + y2 * 2 + x
    return all_leaf_slots[idx]

  return func_get_slot_by_idx


def _get_slot_to_idx(
  func_get_slot_by_idx: Callable[[int, int, int], Slot],
) -> Dict[Slot, Tuple[int, int, int]]:
  """
  given a slot, get (y2, y1, x) in a tuple
  """
  slot_to_idx = {}
  for y1, y2, x in product(range(2), range(2), range(2)):
    slot_to_idx[func_get_slot_by_idx(y1, y2, x)] = (y1, y2, x)
  return slot_to_idx


def _add_area_constraints(
  m: Model,
  v_list: List[Vertex],
  v2var_x: Dict[Vertex, Var],
  v2var_y1: Dict[Vertex, Var],
  v2var_y2: Dict[Vertex, Var],
  func_get_slot_by_idx: Callable[[int, int, int], Slot],
  max_usage_ratio: float,
) -> None:

  # area constraint
  for r in util.RESOURCE_TYPES:
    choose = lambda x, num: x if num == 1 else (1-x)

    for y1, y2, x in product(range(2), range(2), range(2)):
      # convert logic AND to linear constraints
      # prods[v] = choose_y1 AND choose_y2 AND choose_x
      prods = { v : m.add_var(var_type=BINARY, name=f'{v.name}_choose{y1}{y2}{x}') for v in v_list }
      for v in v_list:
        m +=  choose(v2var_y1[v], y1) + choose(v2var_y2[v], y2) + \
              choose(v2var_x[v], x) - 3 * prods[v] >= 0
        m +=  choose(v2var_y1[v], y1) + choose(v2var_y2[v], y2) + \
              choose(v2var_x[v], x) - 3 * prods[v] <= 2

      m += xsum(  prods[v] * v.getVertexAndInboundFIFOArea()[r] for v in v_list ) \
                  <= func_get_slot_by_idx(y1, y2, x).getArea()[r] * max_usage_ratio


def _add_grouping_constraints(
  m: Model,
  grouping_constraints: List[List[Vertex]],
  v2var_x: Dict[Vertex, Var],
  v2var_y1: Dict[Vertex, Var],
  v2var_y2: Dict[Vertex, Var],
) -> None:
  """
  user specifies that certain Vertices must be assigned to the same slot
  """
  for grouping in grouping_constraints:
    for i in range(1, len(grouping)):
      _logger.info(f'[grouping] {grouping[0].name} is grouped with {grouping[i].name}')
      for v2var in [v2var_x, v2var_y1, v2var_y2]:
        m += v2var[grouping[0]] == v2var[grouping[i]]


def _add_pre_assignment(
  m: Model,
  v_list: List[Vertex],
  slot_to_idx: Dict[Slot, Tuple[int, int, int]],
  pre_assignments: Dict[Vertex, Slot],
  v2var_x: Dict[Vertex, Var],
  v2var_y1: Dict[Vertex, Var],
  v2var_y2: Dict[Vertex, Var],
) -> None:
  v_set = set(v_list)
  for v, expect_slot in pre_assignments.items():
    assert v in v_set, f'ERROR: user has forced the location of a non-existing module {v.name}'
    y1, y2, x = slot_to_idx[expect_slot]
    m += v2var_y1[v] == y1
    m += v2var_y2[v] == y2
    m += v2var_x[v] == x


def _add_warm_start_assignment(
  m: Model,
  v_list: List[Vertex],
  slot_to_idx: Dict[Slot, Tuple[int, int, int]],
  warm_start_assignment: Dict[Vertex, Slot],
  v2var_x: Dict[Vertex, Var],
  v2var_y1: Dict[Vertex, Var],
  v2var_y2: Dict[Vertex, Var],
) -> None:
  """
  provide an existing solution as a warm start to the ILP solver
  WARNING: not useful at the moment
  """
  assert all(v in warm_start_assignment for v in v_list), 'ERROR: incomplete initial solution'

  warm_start: List[Tuple[Var, int]] = []
  for v, init_sol_slot in warm_start_assignment.items():
    y1, y2, x = slot_to_idx[init_sol_slot]
    warm_start += [(v2var_y1[v], y1), (v2var_y2[v], y2), (v2var_x[v], x)]
  m.start = warm_start

  m.verbose = 1
  # m.validate_mip_start()
  # if not _logger.isEnabledFor(logging.DEBUG):
  #   m.verbose = 0


def _add_opt_goal(
  m: Model,
  v_list: List[Vertex],
  v2var_x: Dict[Vertex, Var],
  v2var_y1: Dict[Vertex, Var],
  v2var_y2: Dict[Vertex, Var],
) -> None:
  # add optimization goal
  all_edges = util.get_all_edges(v_list)
  e2cost_var = {e: m.add_var(var_type=INTEGER, name=f'intra_{e.name}') for e in all_edges}

  # note pos is different from slot_idx, becasue the x dimension is different from the y dimention
  # we will use |(y1 * 2 + y1) - (y2 * 2 + y2)| + |x1 - x2| to express the hamming distance
  pos_y = lambda v : v2var_y1[v] * 2 + v2var_y2[v]
  pos_x = lambda v : v2var_x[v]
  cost_y = lambda e : pos_y(e.src) - pos_y(e.dst)
  cost_x = lambda e : pos_x(e.src) - pos_x(e.dst)

  for e, cost_var in e2cost_var.items():
    m += cost_var >= cost_y(e) + cost_x(e)
    m += cost_var >= -cost_y(e) + cost_x(e)
    m += cost_var >= cost_y(e) - cost_x(e)
    m += cost_var >= -cost_y(e) - cost_x(e)

  m.objective = minimize(xsum(cost_var * e.width for e, cost_var in e2cost_var.items() ) )


def _get_results(
  m: Model,
  v_list: List[Vertex],
  func_get_slot_by_idx: Callable[[int, int, int], Slot],
  v2var_x: Dict[Vertex, Var],
  v2var_y1: Dict[Vertex, Var],
  v2var_y2: Dict[Vertex, Var],
) -> Optional[Dict[Vertex, Slot]]:
  if m.status == OptimizationStatus.OPTIMAL:
    _logger.info(f'succeed with optimal solution')
  elif m.status == OptimizationStatus.FEASIBLE:
    _logger.info(f'finish with non-optimal solution')
  else:
    _logger.info(f'failed')
    return {}

  # extract results
  next_v2s = {}
  for v in v_list:
    selected_slot = func_get_slot_by_idx(int(v2var_y1[v].x), int(v2var_y2[v].x), int(v2var_x[v].x))
    next_v2s[v] = selected_slot

  return next_v2s