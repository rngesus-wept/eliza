"""Unit tests for secretkeeper."""

import pytest

from .secretkeeper import SecretKeeper
from redbot.testing.conftest import *

@pytest.fixture()
def keeper(config_fr):
  import secretkeeper.secretkeeper as sk_lib
  sk_lib.Config.get_conf = lambda *args, **kwargs: config_fr

  return SecretKeeper(None)
