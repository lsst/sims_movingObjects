#!/usr/bin/env python

import os
import argparse
import logging
import numpy as np

from lsst.sims.movingObjects import Orbits
from lsst.sims.movingObjects import LinearObs
from lsst.sims.movingObjects import DirectObs

from lsst.sims.maf.db import OpsimDatabase
from lsst.sims.maf.batches import getColMap

from lsst.sims.movingObjects import version as smo_version
from lsst.sims.maf import version as maf_version

__all__ = ['readOpsim', 'readOrbits', 'runObs', 'setupArgs']


def readOpsim(opsimfile, constraint=None, footprint='camera', dbcols=None):
    """Read the opsim database.

    Parameters
    ----------
    opsimfile: str
        Name (& path) of the opsim database file.
    constraint: str, opt
        Optional SQL constraint (minus 'where') on the opsim data to read from db.
        Default is None.
    footprint: str, opt
        Footprint option for the final matching of object against OpSim FOV.
        Default 'camera' means that 'rotSkyPos' must be fetched from the db.
        Any other value will not require rotSkyPos.
    dbcols: list of str, opt
        List of additional columns to query from the db and add to the output observations.
        Default None.

    Returns
    -------
    np.ndarray, dictionary
        The OpSim data read from the database, and the dictionary mapping the column names to the data.
    """
    # Read opsim database.
    opsdb = OpsimDatabase(opsimfile)

    colmap = getColMap(opsdb)
    if 'rotSkyPos' not in colmap:
        colmap['rotSkyPos'] = 'rotSkyPos'

    # Set the minimum required columns.
    min_cols = [colmap['mjd'], colmap['night'], colmap['ra'], colmap['dec'],
                colmap['filter'], colmap['exptime'], colmap['seeingGeom'],
                colmap['fiveSigmaDepth']]
    if footprint == 'camera':
        min_cols.append(colmap['rotSkyPos'])
    if dbcols is not None:
        min_cols += dbcols
    min_cols = list(set(min_cols))

    # Check if these minimum required columns are in the database.
    simdata = opsdb.query_columns(tablename=opsdb.defaultTable,
                                  colnames=min_cols,
                                  sqlconstraint=constraint,
                                  numLimit=1)

    # If that was successful, there are some additional columns that can be useful:
    more_cols = [colmap['rotSkyPos'], colmap['seeingEff'], 'solarElong']
    failed_cols = []
    for col in more_cols:
        try:
            simdata = opsdb.query_columns(tablename=opsdb.defaultTable,
                                          colnames=[col], sqlconstraint=constraint, numLimit=1)
        except ValueError:
            failed_cols.append(col)
    for col in failed_cols:
        more_cols.remove(col)
    cols = min_cols + more_cols
    cols = list(set(cols))
    logging.info('Querying for columns:\n %s' % (cols))

    # Go ahead and query for all of the observations.
    simdata = opsdb.fetchMetricData(cols, sqlconstraint=constraint)
    opsdb.close()
    logging.info("Queried data from opsim %s, fetched %d visits." % (opsimfile, len(simdata)))
    return simdata, colmap

def readOrbits(orbitfile):
    """Read the orbits from the orbitfile.

    Parameters
    ----------
    orbitfile: str
        Name (and path) of the orbit file.

    Returns
    -------
    lsst.sims.movingObjects.Orbits
        The orbit object.
    """
    if not os.path.isfile(orbitfile):
        logging.critical("Could not find orbit file %s" % (orbitfile))
    orbits = Orbits()
    orbits.readOrbits(orbitfile)
    logging.info("Read orbit information from %s" % (orbitfile))
    return orbits

def _setupColors(obs, filterlist, orbits):
    # Set up filters
    obs.readFilters(filterlist=filterlist)
    # Calculate all colors ahead of time.
    sednames = np.unique(orbits.orbits['sed_filename'])
    for sedname in sednames:
        obs.calcColors(sedname)
    return obs

def runObs(orbits, simdata, args, colmap):
    """Generate the observations.

    Parameters
    ----------
    orbits: lsst.sims.movingObjects.Orbit
        Orbits for which to calculate observations
    simdata: np.ndarray
        The simulated pointing history data from OpSim
    args: argparse.Namespace
        Arguments from the command line script parser.
    colmap: dict
        Dictionary of the column mappings (from column names here to opsim columns).
    """
    if args.obsType.lower() == 'linear':
        print("Using linear interpolation: ")
        obs = LinearObs(footprint = args.footprint, rFov = args.rFov,
                        xTol = args.xTol, yTol = args.yTol,
                        ephMode = args.ephMode, obsCode=args.obsCode,
                        ephFile = None, ephType=args.ephType,
                        obsTimeCol = colmap['mjd'], obsTimeScale = 'TAI',
                        seeingCol = colmap['seeingGeom'], visitExpTimeCol = colmap['exptime'],
                        obsRA = colmap['ra'], obsDec = colmap['dec'],
                        obsRotSkyPos = colmap['rotSkyPos'], obsDegrees = colmap['raDecDeg'],
                        outfileName = args.obsFile, tstep=args.tStep,
                        obsMetadata = args.obsMetadata)
    elif args.obsType.lower() == "direct":
        print("Using exact ephemeris generation: ")
        obs = DirectObs(footprint = args.footprint, rFov = args.rFov,
                        xTol = args.xTol, yTol = args.yTol,
                        ephMode = args.ephMode, prelimEphMode=args.prelimEphMode, obsCode=args.obsCode,
                        ephFile = None, ephType=args.ephType,
                        obsTimeCol = colmap['mjd'], obsTimeScale = 'TAI',
                        seeingCol = colmap['seeingGeom'], visitExpTimeCol = colmap['exptime'],
                        obsRA = colmap['ra'], obsDec = colmap['dec'],
                        obsRotSkyPos = colmap['rotSkyPos'], obsDegrees = colmap['raDecDeg'],
                        outfileName = args.obsFile, tstep=args.tStep, roughTol=args.roughTol,
                        obsMetadata = args.obsMetadata)
    else:
        raise ValueError("Must use 'Linear' or 'Direct' for the obsType.")
    filterlist = np.unique(simdata['filter'])
    obs = _setupColors(obs, filterlist, orbits)
    obs.run(orbits, simdata)


def setupArgs(parser=None):
    """Parse the command line arguments.

    Parameters
    ----------
    parser: argparse.ArgumentParser, opt
        Generally left at the default (None), but a user could set up their own parser if desired.

    Returns
    -------
    argparse.Namespace
        The argument options.
    """

    if parser is None:
        parser = argparse.ArgumentParser(description="Generate moving object detections.")
    parser.add_argument("--opsimDb", type=str, default=None,
                        help="Opsim output db file (example: kraken_2026.db). Default None.")
    parser.add_argument("--orbitFile", type=str, default=None,
                        help="File containing the moving object orbits. "
                             "See https://github.com/lsst/oorb/blob/lsst-dev/python/README.rst for "
                             "additional documentation on the orbit file format. Default None.")
    parser.add_argument("--outDir", type=str, default='.',
                        help="Output directory for moving object detections. Default '.'")
    parser.add_argument("--obsFile", type=str, default=None,
                        help="Output file name for moving object observations."
                             " Default will build outDir/opsimRun_orbitFile_obs.txt.")
    parser.add_argument("--sqlConstraint", type=str, default='',
                        help="SQL constraint to use to select data from opsimDb. Default no constraint.")
    parser.add_argument("--obsMetadata", type=str, default=None,
                        help="Additional metadata to write into output file. "
                             "The default metadata will combine the opsimDb name, the sqlconstraint, and "
                             "the name of the orbit file; obsMetadata is an optional addition.")
    parser.add_argument("--footprint", type=str, default='circle',
                        help="Type of footprint to use to identify observations of each object. "
                             "Options are 'circle', 'rectangle', or 'camera' (apply camera footprint). "
                             "Default is 'circle' (which will then have a 1.75 deg radius).")
    parser.add_argument("--rFov", type=float, default=1.75,
                        help="If using a circular footprint, this is the radius of the FOV (in degrees). "
                             "Default 1.75 degrees.")
    parser.add_argument("--xTol", type=float, default=5,
                        help="If using a rectangular footprint, this is the tolerance in the RA direction "
                             "(in degrees). Default is 5 degrees.")
    parser.add_argument("--yTol", type=float, default=3,
                        help="If using a rectangular footprint, this is the tolerance in the Dec direction "
                             "(in degrees). Default is 3 degrees.")
    parser.add_argument("--roughTol", type=float, default=20,
                        help="If using direct/exact ephemeris generation, this is the tolerance for the "
                             "preliminary matches between ephemerides and pointings (in degrees). "
                             "Default 20 degrees.")
    parser.add_argument("--obsType", type=str, default='direct',
                       help="Method for generating observations: 'direct' or 'linear'. "
                            "Linear will use linear interpolation between a grid of ephemeris points. "
                            "Direct will first generate rough ephemerides, look for observations within "
                            "roughTol of these points, and then generate exact ephemerides at those times. "
                            "Default 'direct'.")
    parser.add_argument("--obsCode", type=str, default='I11',
                        help="Observatory code for generating observations. "
                             "Default is I11 (Cerro Pachon).")
    parser.add_argument("--tStep", type=float, default=1.0,
                        help="Timestep between ephemeris generation for either the first (rough) stage of "
                             "direct ephemeris generation or the grid for linear interpolation "
                             "ephemerides. Default 1 day.")
    parser.add_argument("--ephMode", type=str, default='nbody',
                        help="2body or nbody mode for ephemeris generation. Default is nbody.")
    parser.add_argument("--prelimEphMode", type=str, default='2body',
                        help="Use either 2body or nbody for preliminary ephemeris generation in the rough "
                             "stage for DirectObs. Default 2body.")
    parser.add_argument("--ephType", type=str, default='basic',
                        help="Generate either 'basic' or 'full' ephemerides from OOrb. "
                             "See https://github.com/lsst/oorb/blob/lsst-dev/python/README.rst for details"
                             "of the contents of 'full' or 'basic' ephemerides. "
                             "Default basic.")
    parser.add_argument("--logFile", type=str, default=None, 
                        help="Send log output to logFile, instead of to console. (default = console)")
    args = parser.parse_args()

    if args.opsimDb is None:
        raise ValueError("Must specify an opsim database output file.")

    if args.orbitFile is None:
        raise ValueError("Must specify an orbit file.")

    # Check interpolation type.
    if args.obsType not in ('linear', 'direct'):
        raise ValueError("Must choose linear or direct observation generation method (obsType).")

    # Add these useful pieces to args.
    args.orbitbase = '.'.join(os.path.split(args.orbitFile)[-1].split('.')[:-1])
    args.opsimRun = os.path.split(args.opsimDb)[-1].replace('_sqlite.db', '').replace('.db', '')

    # Set up obsFile if not specified.
    if args.obsFile is None:
        args.obsFile = os.path.join(args.outDir, '%s__%s_obs.txt' % (args.opsimRun, args.orbitbase))
    else:
        args.obsFile = os.path.join(args.outDir, args.obsFile)

    # Build some provenance metadata to add to output file.
    obsMetadata = "Opsim %s" % args.opsimRun
    if len(args.sqlConstraint) > 0:
        obsMetadata += ' selected with sqlconstraint %s' % (args.sqlConstraint)
    obsMetadata += ' + Orbitfile %s' % args.orbitbase
    if args.obsMetadata is not None:
        obsMetadata += '\n# %s' % args.obsMetadata
    args.obsMetadata = obsMetadata

    return args


if __name__ == '__main__':

    # Parser command
    args = setupArgs()

    # Send info and above logging messages to the console or logfile.
    if args.logFile is not None:
        logging.basicConfig(filename=args.logFile, level=logging.INFO)
    else:
        logging.basicConfig(level=logging.INFO)

    logging.info('# sims_movingObjects version %s (%s)' % (smo_version.__version__,
                                                           smo_version.__fingerprint__))
    logging.info('# sims_maf version: %s (%s)' % (maf_version.__version__,
                                                  maf_version.__fingerprint__))

    # Read orbits.
    orbits = readOrbits(args.orbitFile)

    # Read opsim data
    opsimdata, colmap = readOpsim(args.opsimDb, constraint=args.sqlConstraint,
                                  footprint=args.footprint, dbcols=None)

    # Generate ephemerides.
    runObs(orbits, opsimdata, args, colmap)

    logging.info('Completed successfully.')
