#!/usr/bin/env python
# Eclipse SUMO, Simulation of Urban MObility; see https://eclipse.org/sumo
# Copyright (C) 2012-2020 German Aerospace Center (DLR) and others.
# This program and the accompanying materials are made available under the
# terms of the Eclipse Public License 2.0 which is available at
# https://www.eclipse.org/legal/epl-2.0/
# This Source Code may also be made available under the following Secondary
# Licenses when the conditions for such availability set forth in the Eclipse
# Public License 2.0 are satisfied: GNU General Public License, version 2
# or later which is available at
# https://www.gnu.org/licenses/old-licenses/gpl-2.0-standalone.html
# SPDX-License-Identifier: EPL-2.0 OR GPL-2.0-or-later

# @file    routeSampler.py
# @author  Jakob Erdmann
# @date    2020-02-07

"""
Samples routes from a given set to fullfill specified counting data (edge counts or turn counts)
"""
from __future__ import absolute_import
from __future__ import print_function

import os
import sys
import random
from collections import defaultdict

if 'SUMO_HOME' in os.environ:
    sys.path.append(os.path.join(os.environ['SUMO_HOME'], 'tools'))
import sumolib  # noqa


def get_options(args=None):
    parser = sumolib.options.ArgumentParser(description="Sample routes to match counts")
    parser.add_argument("-r", "--route-files", dest="routeFiles",
                        help="Input route file file")
    parser.add_argument("-t", "--turn-files", dest="turnFiles",
                        help="Input turn-count file")
    parser.add_argument("-d", "--edgedata-files", dest="edgeDataFiles",
                        help="Input edgeData file file (for counts)")
    parser.add_argument("--edgedata-attribute", dest="edgeDataAttr", default="entered",
                        help="Read edgeData counts from the given attribute")
    parser.add_argument("--turn-attribute", dest="turnAttr", default="count",
                        help="Read turning counts from the given attribute")
    parser.add_argument("--turn-max-gap", type=int, dest="turnMaxGap", default=0,
                        help="Allow at most a gap of INT edges between from-edge and to-edge")
    parser.add_argument("-o", "--output-file", dest="out", default="out.rou.xml",
                        help="Output route file")
    parser.add_argument("--prefix", dest="prefix", default="",
                        help="prefix for the vehicle ids")
    parser.add_argument("-a", "--attributes", dest="vehattrs", default="",
                        help="additional vehicle attributes")
    parser.add_argument("-s", "--seed", type=int, default=42,
                        help="random seed")
    parser.add_argument("--mismatch-output", dest="mismatchOut",
                        help="write cout-data with overflow/underflow information to FILE")
    parser.add_argument("--optimize",
                        help="set optimization method level (full, INT boundary)")
    parser.add_argument("--optimize-input", dest="optimizeInput", action="store_true", default=False,
                        help="Skip resampling and run optimize directly on the input routes")
    parser.add_argument("--geh-ok", dest="gehOk", default=5,
                        help="threshold for acceptable GEH values")
    parser.add_argument("-f", "--write-flows", dest="writeFlows",
                        help="write flows with the give style instead of vehicles [number|probability]")
    parser.add_argument("-i", "--write-route-ids", dest="writeRouteIDs", action="store_true", default=False,
                        help="write routes with ids")
    parser.add_argument("-u", "--write-route-distribution", dest="writeRouteDist",
                        help="write routeDistribution with the given ID instead of individual routes")
    parser.add_argument("-v", "--verbose", action="store_true", default=False,
                        help="tell me what you are doing")

    options = parser.parse_args(args=args)
    if (options.routeFiles is None or
            (options.turnFiles is None and options.edgeDataFiles is None)):
        parser.print_help()
        sys.exit()
    if options.writeRouteIDs and options.writeRouteDist:
        sys.stderr.write("Only one of the options --write-route-ids and --write-route-distribution may be used")
        sys.exit()
    if options.writeFlows not in [None, "number", "probability"]:
        sys.stderr.write("Options --write-flows only accepts arguments 'number' and 'probability'")
        sys.exit()

    options.routeFiles = options.routeFiles.split(',')
    options.turnFiles = options.turnFiles.split(',') if options.turnFiles is not None else []
    options.edgeDataFiles = options.edgeDataFiles.split(',') if options.edgeDataFiles is not None else []
    if options.vehattrs and options.vehattrs[0] != ' ':
        options.vehattrs = ' ' + options.vehattrs

    if options.optimize is not None:
        try:
            import scipy.optimize  # noqa
            if options.optimize != "full":
                try:
                    options.optimize = int(options.optimize)
                except Exception:
                    print("Option optimize requires the value 'full' or an integer", file=sys.stderr)
                    sys.exit(1)
        except ImportError:
            print("Cannot use optimization (scipy not installed)", file=sys.stderr)
            sys.exit(1)

    if options.optimizeInput and type(options.optimize) != int:
        print("Option --optimize-input requires an integer argument for --optimize", file=sys.stderr)
        sys.exit(1)

    return options


class CountData:
    def __init__(self, count, edgeTuple, allRoutes):
        self.origCount = count
        self.count = count
        self.edgeTuple = edgeTuple
        self.routeSet = set()
        for routeIndex, edges in enumerate(allRoutes.unique):
            if self.routePasses(edges):
                self.routeSet.add(routeIndex)
        if self.count > 0 and not self.routeSet:
            print("Warning: no routes pass edge '%s' (count %s)" %
                  (' '.join(self.edgeTuple), self.count), file=sys.stderr)

    def routePasses(self, edges):
        try:
            i = edges.index(self.edgeTuple[0])
            maxDelta = sumolib.options.getOptions().turnMaxGap + 1
            for edge in self.edgeTuple[1:]:
                i2 = edges.index(edge, i)
                if i2 - i > maxDelta:
                    return False
                i = i2
        except ValueError:
            # first edge not in route
            return False
        return True

    def sampleOpen(self, openRoutes, routeCounts):
        cands = list(self.routeSet.intersection(openRoutes))
        assert(cands)
        probs = [routeCounts[i] for i in cands]
        x = random.random * sum(probs)
        seen = 0
        for route, prob in zip(cands, probs):
            seen += prob
            if seen >= x:
                return route
        assert(False)


def parseTurnCounts(fnames, allRoutes, attr):
    result = []
    for fname in fnames:
        for interval in sumolib.xml.parse(fname, 'interval', heterogeneous=True):
            for edgeRel in interval.edgeRelation:
                via = [] if edgeRel.via is None else edgeRel.via.split(' ')
                edges = [edgeRel.attr_from] + via + [edgeRel.to]
                result.append(CountData(int(getattr(edgeRel, attr)), tuple(edges), allRoutes))
    return result


def parseEdgeCounts(fnames, allRoutes, attr):
    result = []
    for fname in fnames:
        for interval in sumolib.xml.parse(fname, 'interval'):
            for edge in interval.edge:
                result.append(CountData(int(float(getattr(edge, attr))),
                                        (edge.id,), allRoutes))
    return result


def parseTimeRange(fnames):
    begin = 1e20
    end = 0
    for fname in fnames:
        for interval in sumolib.xml.parse(fname, 'interval'):
            begin = min(begin, float(interval.begin))
            end = max(end, float(interval.end))
    return begin, end


def hasCapacity(dataIndices, countData):
    for i in dataIndices:
        if countData[i].count == 0:
            return False
    return True


def updateOpenRoutes(openRoutes, routeUsage, countData):
    return set(filter(lambda r: hasCapacity(routeUsage[r], countData), openRoutes))


def updateOpenCounts(openCounts, countData, openRoutes):
    return set(filter(lambda i: countData[i].routeSet.intersection(openRoutes), openCounts))


def optimize(options, countData, routes, usedRoutes, routeUsage):
    """ use relaxtion of the ILP problem for picking the number of times that each route is used
    x = usageCount vector (count for each route index)
    c = weight vector (vector of 1s)
    A_eq = routeUsage encoding
    b_eq = counts

    Rationale:
      c: costs for using each route,
         when minimizing x @ c, routes that pass multiple counting stations are getting an advantage

    """
    import scipy.optimize as opt
    import scipy.version
    import numpy as np

    k = routes.number
    m = len(countData)

    priorRouteCounts = getRouteCounts(routes, usedRoutes)

    if options.optimize == "full":
        # allow changing all prior usedRoutes
        bounds = None
    else:
        u = int(options.optimize)
        if u == 0:
            print("Optimization skipped")
            return
        # limited optimization: change prior routeCounts by at most u per route
        bounds = [(max(0, p - u), p + u) for p in priorRouteCounts] + [(0, None)] * m

    # Ax <= b
    # x + s = b
    # min s
    # -> x2 = [x, s]

    A = np.zeros((m, k))
    for i in range(0, m):
        for j in range(0, k):
            A[i][j] = int(j in countData[i].routeSet)
    A_eq = np.concatenate((A, np.identity(m)), 1)

    # constraint: achieve counts
    b = np.asarray([cd.origCount for cd in countData])

    # minimization objective
    c = np.concatenate((np.zeros(k), np.ones(m)))  # [x, s], only s counts for minimization

    # set x to prior counts and slack to deficit (otherwise solver may fail to any find soluton
    x0 = priorRouteCounts + [cd.origCount - cd.count for cd in countData]

    # print("k=%s" % k)
    # print("m=%s" % m)
    # print("A_eq (%s) %s" % (A_eq.shape, A_eq))
    # print("b (%s) %s" % (len(b), b))
    # print("c (%s) %s" % (len(c), c))
    # print("bounds (%s) %s" % (len(bounds) if bounds is not None else "-", bounds))
    # print("x0 (%s) %s" % (len(x0), x0))

    linProgOpts = {}
    if options.verbose:
        linProgOpts["disp"] = True

    try:
        res = opt.linprog(c, A_eq=A_eq, b_eq=b, bounds=bounds, x0=x0, options=linProgOpts)
    except TypeError:
        if options.verbose:
            print("Warning: Scipy version %s does not support initial guess for opt.linprog. Optimization may fail"
                  % scipy.version.version, file=sys.stderr)
        res = opt.linprog(c, A_eq=A_eq, b_eq=b, bounds=bounds, options=linProgOpts)

    del usedRoutes[:]
    if res.success:
        print("Optimization succeeded")
        routeCounts = res.x[:k]  # cut of slack variables
        # slack = res.x[k:]
        # print("routeCounts (n=%s, sum=%s, intSum=%s, roundSum=%s) %s" % (
        #    len(routeCounts),
        #    sum(routeCounts),
        #    sum(map(int, routeCounts)),
        #    sum(map(round, routeCounts)),
        #    routeCounts))
        # print("slack (n=%s, sum=%s) %s" % (len(slack), sum(slack), slack))
        usedRoutes.extend(sum([[i] * int(round(c)) for i, c in enumerate(routeCounts)], []))
        random.shuffle(usedRoutes)
        # print("#usedRoutes=%s" % len(usedRoutes))
        # update countData
    else:
        print("Optimization failed")


class Routes:
    def __init__(self, routefiles):
        self.all = []
        for routefile in routefiles:
            self.all += [tuple(r.edges.split()) for r in sumolib.xml.parse(routefile, 'route')]
        self.unique = sorted(list(set(self.all)))
        self.number = len(self.unique)
        self.edges2index = dict([(e, i) for i, e in enumerate(self.unique)])
        self.loadedCounts = [0] * len(self.edges2index)  # route index to count
        for e in self.all:
            self.loadedCounts[self.edges2index[e]] += 1


def resetCounts(usedRoutes, routeUsage, countData):
    for cd in countData:
        cd.count = cd.origCount
    for r in usedRoutes:
        for i in routeUsage[r]:
            countData[i].count -= 1


def getRouteCounts(routes, usedRoutes):
    result = [0] * routes.number
    for r in usedRoutes:
        result[r] += 1
    return result


def main(options):
    if options.seed:
        random.seed(options.seed)

    routes = Routes(options.routeFiles)

    # store which routes are passing each counting location (using route index)
    countData = (parseTurnCounts(options.turnFiles, routes, options.turnAttr)
                 + parseEdgeCounts(options.edgeDataFiles, routes, options.edgeDataAttr))

    # store which counting locations are used by each route (using countData index)
    routeUsage = [set() for r in routes.unique]
    for i, cd in enumerate(countData):
        for routeIndex in cd.routeSet:
            routeUsage[routeIndex].add(i)

    if options.verbose:
        print("Loaded %s routes (%s distinct)" % (len(routes.all), routes.number))
        edgeCount = sumolib.miscutils.Statistics("route edge count", histogram=True)
        detectorCount = sumolib.miscutils.Statistics("route detector count", histogram=True)
        for i, edges in enumerate(routes.unique):
            edgeCount.add(len(edges), i)
            detectorCount.add(len(routeUsage[i]), i)
        print("input %s" % edgeCount)
        print("input %s" % detectorCount)

    # pick a random couting location and select a new route that passes it until
    # all counts are satisfied or no routes can be used anymore
    openRoutes = set(range(0, routes.number))
    openCounts = set(range(0, len(countData)))
    openRoutes = updateOpenRoutes(openRoutes, routeUsage, countData)
    openCounts = updateOpenCounts(openCounts, countData, openRoutes)

    usedRoutes = []
    if options.optimizeInput:
        usedRoutes = [routes.edges2index[e] for e in routes.all]
        resetCounts(usedRoutes, routeUsage, countData)
    else:
        while openCounts:
            cd = countData[random.sample(openCounts, 1)[0]]
            routeIndex = random.sample(cd.routeSet.intersection(openRoutes), 1)[0]
            usedRoutes.append(routeIndex)
            for dataIndex in routeUsage[routeIndex]:
                countData[dataIndex].count -= 1
            openRoutes = updateOpenRoutes(openRoutes, routeUsage, countData)
            openCounts = updateOpenCounts(openCounts, countData, openRoutes)

    hasMismatch = sum([cd.count for cd in countData]) > 0
    if hasMismatch and options.optimize is not None:
        optimize(options, countData, routes, usedRoutes, routeUsage)
        resetCounts(usedRoutes, routeUsage, countData)

    begin, end = parseTimeRange(options.turnFiles + options.edgeDataFiles)
    if usedRoutes:
        with open(options.out, 'w') as outf:
            sumolib.writeXMLHeader(outf, "$Id$", "routes")  # noqa
            period = (end - begin) / len(usedRoutes)
            depart = begin
            routeCounts = getRouteCounts(routes, usedRoutes)
            if options.writeRouteIDs:
                for routeIndex in sorted(set(usedRoutes)):
                    outf.write('    <route id="%s" edges="%s"/> <!-- %s -->\n' % (
                        routeIndex, ' '.join(routes.unique[routeIndex]), routeCounts[routeIndex]))
                outf.write('\n')
            elif options.writeRouteDist:
                outf.write('    <routeDistribution id="%s"/>\n' % options.writeRouteDist)
                for routeIndex in sorted(set(usedRoutes)):
                    outf.write('        <route id="%s" edges="%s" probability="%s"/>\n' % (
                        routeIndex, ' '.join(routes.unique[routeIndex]), routeCounts[routeIndex]))
                outf.write('    </routeDistribution>\n\n')

            routeID = options.writeRouteDist
            if options.writeFlows is None:
                for i, routeIndex in enumerate(usedRoutes):
                    if options.writeRouteIDs:
                        routeID = routeIndex
                    if routeID is not None:
                        outf.write('    <vehicle id="%s%s" depart="%.2f" route="%s"%s/>\n' % (
                            options.prefix, i, depart, routeID, options.vehattrs))
                    else:
                        outf.write('    <vehicle id="%s%s" depart="%.2f"%s>\n' % (
                            options.prefix, i, depart, options.vehattrs))
                        outf.write('        <route edges="%s"/>\n' % ' '.join(routes.unique[routeIndex]))
                        outf.write('    </vehicle>\n')
                    depart += period
            else:
                routeDeparts = defaultdict(list)
                for routeIndex in usedRoutes:
                    routeDeparts[routeIndex].append(depart)
                    depart += period
                if options.writeRouteDist:
                    totalCount = sum(routeCounts)
                    probability = totalCount / (end - begin)
                    flowID = options.prefix + options.writeRouteDist
                    if options.writeFlows == "number" or probability > 1.001:
                        repeat = 'number="%s"' % totalCount
                        if options.writeFlows == "probability":
                            sys.stderr.write("Warning: could not write flow %s with probability %.2f\n" %
                                             (flowID, probability))
                    else:
                        repeat = 'probability="%s"' % probability
                    outf.write('    <flow id="%s" begin="%.2f" end="%.2f" %s route="%s"%s/>\n' % (
                        flowID, begin, end, repeat,
                        options.writeRouteDist, options.vehattrs))
                else:
                    for routeIndex in sorted(set(usedRoutes)):
                        fBegin = min(routeDeparts[routeIndex])
                        fEnd = max(routeDeparts[routeIndex] + [fBegin + 1.0])
                        probability = routeCounts[routeIndex] / (fEnd - fBegin)
                        flowID = "%s%s" % (options.prefix, routeIndex)
                        if options.writeFlows == "number" or probability > 1.001:
                            repeat = 'number="%s"' % routeCounts[routeIndex]
                            if options.writeFlows == "probability":
                                sys.stderr.write("Warning: could not write flow %s with probability %.2f\n" % (
                                    flowID, probability))
                        else:
                            repeat = 'probability="%s"' % probability
                        if options.writeRouteIDs:
                            outf.write('    <flow id="%s" begin="%.2f" end="%.2f" %s route="%s"%s/>\n' % (
                                flowID, fBegin, fEnd, repeat,
                                routeIndex, options.vehattrs))
                        else:
                            outf.write('    <flow id="%s%s" begin="%.2f" end="%.2f" %s%s>\n' % (
                                options.prefix, routeIndex,
                                fBegin, fEnd, repeat,
                                options.vehattrs))
                            outf.write('        <route edges="%s"/>\n' % ' '.join(routes.unique[routeIndex]))
                            outf.write('    </flow>\n')

            outf.write('</routes>\n')

    underflow = sumolib.miscutils.Statistics("underflow locations")
    overflow = sumolib.miscutils.Statistics("overflow locations")
    gehStats = sumolib.miscutils.Statistics("GEH")
    numGehOK = 0.0
    hourFraction = (end - begin) / 3600.0
    totalCount = 0
    for cd in countData:
        localCount = cd.origCount - cd.count
        totalCount += localCount
        if cd.count > 0:
            underflow.add(cd.count, cd.edgeTuple)
        elif cd.count < 0:
            overflow.add(cd.count, cd.edgeTuple)
        origHourly = cd.origCount / hourFraction
        localHourly = localCount / hourFraction
        geh = sumolib.miscutils.geh(origHourly, localHourly)
        if geh < options.gehOk:
            numGehOK += 1
        gehStats.add(geh, "[%s] %s %s" % (
            ' '.join(cd.edgeTuple), int(origHourly), int(localHourly)))

    print("Wrote %s routes (%s distinct) achieving total count %s at %s locations. GEH<%s for %.2f%%" % (
        len(usedRoutes), len(set(usedRoutes)), totalCount, len(countData),
        options.gehOk, 100 * numGehOK / len(countData)))

    if options.verbose:
        edgeCount = sumolib.miscutils.Statistics("route edge count", histogram=True)
        detectorCount = sumolib.miscutils.Statistics("route detector count", histogram=True)
        for i, r in enumerate(usedRoutes):
            edgeCount.add(len(routes.unique[r]), i)
            detectorCount.add(len(routeUsage[r]), i)
        print("result %s" % edgeCount)
        print("result %s" % detectorCount)
        print(gehStats)

    if underflow.count() > 0:
        print("Warning: %s (total %s)" % (underflow, sum(underflow.values)))
    if overflow.count() > 0:
        print("Warning: %s (total %s)" % (overflow, sum(overflow.values)))

    if options.mismatchOut:
        with open(options.mismatchOut, 'w') as outf:
            sumolib.writeXMLHeader(outf, "$Id$")  # noqa
            outf.write('<data>\n')
            outf.write('    <interval id="deficit" begin="0" end="3600">\n')
            for cd in countData:
                if len(cd.edgeTuple) == 1:
                    outf.write('        <edge id="%s" measuredCount="%s" deficit="%s"/>\n' % (
                        cd.edgeTuple[0], cd.origCount, cd.count))
                elif len(cd.edgeTuple) == 2:
                    outf.write('        <edgeRelation from="%s" to="%s" measuredCount="%s" deficit="%s"/>\n' % (
                        cd.edgeTuple[0], cd.edgeTuple[1], cd.origCount, cd.count))
                else:
                    print("Warning: output for edge relations with more than 2 edges not supported (%s)" % cd.edgeTuple,
                          file=sys.stderr)
            outf.write('    </interval>\n')
            outf.write('</data>\n')


if __name__ == "__main__":
    main(get_options())
