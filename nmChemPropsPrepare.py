#!/usr/bin/python

# 06/04/2019 Bingyin Hu

import requests
import xlrd
from pymongo import MongoClient
import logging

class nmChemPropsPrepare():
    def __init__(self):
        # load logger
        logging.basicConfig(filename='nmChemPropsPrepare.log',
                            format='%(asctime)s - %(levelname)s - %(message)s',
                            level = logging.INFO
                           )
        self.loadGSconfig()
        self.loadMGconfig()
        # downloadGS
        self.downloadGS()
        # if we changed something in mongo, gsUpdate list will record these changes
        # admins need to manually address these changes in the google spreadsheet
        self.gsUpdate = []
        # prepare filler and polymer data
        self.filler = dict()
        self.polymer = dict()
        self.prepFiller()
        self.prepPolymer()
        # mongo init
        self.client = MongoClient('mongodb://%s:%s@localhost:27017/tracking?authSource=admin'
                                  %(self.env['NM_MONGO_USER'],
                                    self.env['NM_MONGO_PWD']
                                   )
                                 )

    # load google spreadsheet configurations
    def loadGSconfig(self):
        # read gs.config for configurations
        with open("gs.config", "r") as f:
            confs = f.read().split('\n')
        self.url_format = confs[0]
        self.key = confs[1]
        self.format = "xlsx"
        # gids
        self.gids = dict()
        for i in range(2,len(confs)):
            kv = confs[i]
            k = kv.split(':')[0].strip()
            v = kv.split(':')[1].strip()
            self.gids[k] = v
    
    # load mongo configurations
    def loadMGconfig(self):
        self.env = dict()
        # read mongo.config for configurations
        with open("mongo.config", "r") as f:
            confs = f.read().split('\n')
        for i in range(len(confs)):
            kv = confs[i]
            k = kv.split(':')[0].strip()
            v = kv.split(':')[1].strip()
            self.env[k] = v

    # download google spreadsheets
    def downloadGS(self):
        for fname in self.gids:
            resp = requests.get(self.url_format %(self.key,
                                                  self.format,
                                                  self.gids[fname]
                                                 )
                               )
            with open(fname + ".xlsx", "wb") as f:
                f.write(resp.content)
                logging.info("%s sheet is downloaded as %s.xlsx" %(fname, fname))

    # prepare ChemProps.polymer data
    def prepPolymer(self):
        xlfile = xlrd.open_workbook("matrixRaw.xlsx") # change the filename if gs.config is changed
        sheet = xlfile.sheets()[0] # one sheet per xlsx file
        header = sheet.row_values(0) # SMILES;uSMILES;std_name;density(g/cm3);density_std_err(g/cm3);abbreviations;synonyms;tradenames
        # create a map for headers
        hmap = {}
        for i in range(len(header)):
            hmap[header[i]] = i
        # loop
        for row in range(1, sheet.nrows):
            rowdata = sheet.row_values(row)
            # skip the unfilled items
            if (len(rowdata[hmap['abbreviations']]) == 0 and
                len(rowdata[hmap['synonyms']]) == 0 and
                len(rowdata[hmap['tradenames']]) == 0):
                continue
            # otherwise save the data to self.polymer
            if rowdata[hmap['uSMILES']] not in self.polymer:
                self.polymer[rowdata[hmap['uSMILES']]] = {
                    "_id": rowdata[hmap['uSMILES']],
                    "_stdname": rowdata[hmap['std_name']],
                    "_abbreviations": [],
                    "_synonyms": [],
                    "_tradenames": [],
                    "_density": rowdata[hmap['density(g/cm3)']]
                }
            # abbreviations
            if len(rowdata[hmap['abbreviations']]) > 0:
                self.polymer[rowdata[hmap['uSMILES']]]['_abbreviations'] = self.striplist(rowdata[hmap['abbreviations']].split(';'))
            # synonyms
            if len(rowdata[hmap['synonyms']]) > 0:
                self.polymer[rowdata[hmap['uSMILES']]]['_synonyms'] = self.striplist(rowdata[hmap['synonyms']].split(';'))
            # tradenames
            if len(rowdata[hmap['tradenames']]) > 0:
                self.polymer[rowdata[hmap['uSMILES']]]['_tradenames'] = self.striplist(rowdata[hmap['tradenames']].split(';'))
        # log
        logging.info("Finish processing the polymer data.")


    # prepare ChemProps.filler data
    def prepFiller(self):
        xlfile = xlrd.open_workbook("fillerRaw.xlsx") # change the filename if gs.config is changed
        sheet = xlfile.sheets()[0] # one sheet per xlsx file
        header = sheet.row_values(0) # nm_entry/std_name/density_g_cm3
        # create a map for headers
        hmap = {}
        for i in range(len(header)):
            hmap[header[i]] = i
        # loop
        for row in range(1, sheet.nrows):
            rowdata = sheet.row_values(row)
            if rowdata[hmap['std_name']] not in self.filler:
                self.filler[rowdata[hmap['std_name']]] = {"_id":rowdata[hmap['std_name']], "_density": rowdata[hmap['density_g_cm3']], "_alias":[]}
            self.filler[rowdata[hmap['std_name']]]['_alias'].append(rowdata[hmap['nm_entry']])
        # log
        logging.info("Finish processing the filler data.")

    # update MongoDB
    def updateMongoDB(self):
        dbnames = self.client.list_database_names() # check if db exists
        initPolymer = False # a flag inidicating whether this is the first time creating the ChemProps.polymer collection
        initFiller = False # a flag inidicating whether this is the first time creating the ChemProps.filler collection
        if u'ChemProps' not in dbnames:
            initPolymer = True
            initFiller = True
        cp = self.client.ChemProps
        clctnames = cp.list_collection_names() # check if collection exists
        # if ChemProps exists
        if not initPolymer and 'polymer' not in clctnames:
            initPolymer = True
        if not initPolymer and 'filler' not in clctnames:
            initFiller = True
        ## first creation cases (polymer)
        if initPolymer:
            pol = cp.polymer
            posted_polymer = pol.insert_many(self.polymer.values())
            logging.info("The polymer collection in the ChemProps DB is created for the first time.")
        ## update cases (polymer)
        else:
            # loop through the items in the self.polymer dict, see if everything matches
            for uSMILES in self.polymer:
                gsData = self.polymer[uSMILES] # google spreadsheet data
                mgData = cp.polymer.find({"_id": uSMILES})[0] # mongo data, find by _id
                # continue if there is no difference between gsData and mgData
                if gsData == mgData:
                    continue
                # otherwise, find the difference
                # if gsData is a superset of mgData, update mgData
                # if mgData is a superset of gsData, record the difference in self.gsUpdate
                # the structure of result see compareDict() instruction
                d1name = 'google sheet'
                d2name = 'mongo'
                result = self.compareDict(d1 = gsData,
                                          d1name = d1name,
                                          d2 = mgData,
                                          d2name = d2name,
                                          imtbKeys = {'_id',
                                                      '_stdname',
                                                      '_density'
                                                     }
                                         )
                # required updates for gsData go to self.gsUpdate
                for change in result['google sheet']:
                    self.gsUpdate.append(
                        "%s %s to %s of the polymer with uSMILES: %s."
                        %(change[0], change[2], change[1], uSMILES))
                # apply/update the changes
                for change in result[d2name]:
                    cp.polymer.update(
                        {"_id": uSMILES},
                        {"%s" %(change[0]): { change[1]: change[2]}}
                    )
                    logging.info("Apply %s with value %s to %s of the polymer with uSMILES: %s in ChemProps."
                                 %(change[0], change[2], change[1], uSMILES)
                                )
            # end of the loop
        ## first creation cases (filler)
        if initFiller:
            fil = cp.filler
            posted_filler = fil.insert_many(self.filler.values())
            logging.info("The filler collection in the ChemProps DB is created for the first time.")
        ## update cases (filler)
        else:
            # loop through the items in the self.filler dict, see if everything matches
            for std_name in self.filler:
                # same structure as self.polymer
                gsData = self.filler[std_name]
                mgData = cp.filler.find({"_id": std_name})[0]
                if gsData == mgData:
                    continue
                d1name = 'google sheet'
                d2name = 'mongo'
                result = self.compareDict(d1 = gsData,
                                          d1name = d1name,
                                          d2 = mgData,
                                          d2name = d2name,
                                          imtbKeys = {'_id',
                                                      '_density'
                                                     }
                                         )
                # required updates for gsData go to self.gsUpdate
                for change in result['google sheet']:
                    self.gsUpdate.append(
                        "%s %s to %s of the filler with std_name: %s."
                        %(change[0], change[2], change[1], std_name))
                # apply/update the changes
                for change in result[d2name]:
                    cp.filler.update(
                        {"_id": std_name},
                        {"%s" %(change[0]): { change[1]: change[2]}}
                    )
                    logging.info("Apply %s with value %s to %s of the filler with std_name: %s in ChemProps."
                                 %(change[0], change[2], change[1], std_name)
                                )
            # end of the loop
        ## append gsUpdate records as WARNING to the log
        for rec in self.gsUpdate:
            logging.warn(rec)
        self.gsUpdate = [] # reset gsUpdate

    # remove leading and trailing white spaces
    def striplist(self, mylist):
        for i in range(len(mylist)):
            mylist[i] = mylist[i].strip()
        return mylist

    # compare two dicts, need to specify the keys to the immutable objects,
    # the function returns a result dict that indicates objects that do not
    # exist in the current dict but exist in the other dict for each dict.
    # DO NOT SUPPORT NESTED DICTS
    # example:
    # d1 = {'k1': [1,2], 'k2': 'new'}
    # d2 = {'k1': [1,3], 'k2': 'old'}
    # result = {'d1': [('$addToSet', 'k1', 3)], 'd2': [('$addToSet', 'k1', 2), ('$set', 'k2', 'new')]}
    def compareDict(self, d1, d1name, d2, d2name, imtbKeys):
        result = {d1name: [], d2name: []} # init output dict
        # prepPolymer guarantees d1 and d2 will have the same keys set even if
        # some keys will have empty string or list
        allKeys = set(d1.keys())
        for key in allKeys:
            # immutables always trust d1 has the latest version
            if key in imtbKeys:
                if d1[key] != d2[key]:
                    result[d2name].append(('$set', key, d1[key]))
            # non immutables
            else:
                # use set.difference() function to get the result
                # in d1[key] not in d2[key]
                d1subd2 = set(d1[key]).difference(set(d2[key]))
                # in d2[key] not in d1[key]
                d2subd1 = set(d2[key]).difference(set(d1[key]))
                # update result
                for addTod2 in d1subd2:
                    result[d2name].append(('$addToSet', key, addTod2))
                for addTod1 in d2subd1:
                    result[d1name].append(('$addToSet', key, addTod1))
        return result

if __name__ == '__main__':
    nm = nmChemPropsPrepare()
