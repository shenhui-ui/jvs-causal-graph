# -*- coding: utf-8 -*-
import io
import os

marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "direct-marker.txt")
with io.open(marker, "w", encoding="utf-8") as f:
    f.write("DIRECT-OK argv-run\n")
print("done")
