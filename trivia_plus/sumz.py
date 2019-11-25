import functools
import itertools

def CharToNum(card):
  return sum(
      2 ** idx for idx, char in enumerate('QWASZX') if char in card)

def NumToChar(num):
  return ''.join(
      'QWASZX'[idx] for idx in range(6) if 2 ** idx & num)

def Solve(*cards):
  nums = list(map(CharToNum, cards))
  for count in range(3, len(cards)):
    for combo in itertools.combinations(nums, count):
      if functools.reduce(lambda x, y: x ^ y, combo) == 0:
        print(' + '.join(map(NumToChar,combo)))

def AllOrders(foo):
  if len(foo) == 2:
    return f'({foo}|{"".join(reversed(foo))})'
  else:
    acc = []
    for idx in range(len(foo)):
      acc.append(foo[idx] + AllOrders(foo[:idx] + foo[idx+1:]))
    return '(' + '|'.join(acc) + ')'
