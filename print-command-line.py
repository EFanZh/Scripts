#!/usr/bin/env python3                                                                                                                                                              

import shlex
import sys


def main():
    print(*map(shlex.quote, sys.argv))


if __name__ == '__main__':
    main()
