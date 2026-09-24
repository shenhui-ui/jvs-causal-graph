# -*- coding: utf-8 -*-
import io
import os
import sys

marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyrun-marker-test.txt")
with io.open(marker, "w", encoding="utf-8") as f:
    f.write("MARKER-OK argv=%r cwd=%r\n" % (sys.argv, os.getcwd()))
print("marker written")
