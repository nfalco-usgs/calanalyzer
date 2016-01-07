#!/usr/bin/env python

'''
Created on Sep 18, 2015

@author: nfalco
'''

from obspy.core import read, UTCDateTime, Stream
import numpy as np
import math
import sys
import commands
import matplotlib.pyplot as plt
from obspy.signal import cornFreq2Paz
from scipy.optimize import fmin
import logging
import numpy
import psycopg2
import spectrum
from inspect import trace
import obspy


class ComputeCalibrations(object):
    
    def __init__(self, dataInLoc, dataOutLoc, startdate, julianday, cal_duration, cal_id, outChannel, network, station, location, dbconn, sentype = None):
        self.dataInLoc = dataInLoc #Location where the input data is located 
        self.dataOutLoc = dataOutLoc #Location where the output seed data is located
        self.startdate = startdate #Start data for the calibration
        self.julianday = julianday
        self.cal_duration = cal_duration #Duration of the calibration in milliseconds 
        self.cal_id = cal_id #Database primary key of the calibration
        self.outChannel = outChannel #Channel for which the calibration was calculated (eg. BHZ, BH1, BH2, etc.)
        self.network = network
        self.station = station
        self.location = location
        self.sentype = sentype #Manual override for sensor type
        self.dbconn = dbconn #Database connection object
        #Setup logging for calibrations
        self.stepcal_logger = logging.getLogger('ComputeCalibrations.stepcal')
        self.sinecal_logger = logging.getLogger('ComputeCalibrations.sinecal')

    def computeSineCal(self):
        try:
            #Read in BH and BC
            dataIN = read(self.dataInLoc)
            dataOUT = read(self.dataOutLoc)
            #Convert start date to UTC
            stime = UTCDateTime(str(self.startdate))
    
            #Trim data to only grab the sine calibration
            dataIN.trim(starttime=stime, endtime=stime + self.cal_duration)
            dataOUT.trim(starttime=stime, endtime=stime + self.cal_duration)
        
            #Calculate RMS of both traces and divide
            dataINRMS = math.sqrt(2.)*sum(np.square(dataIN[0].data))
            dataINRMS /= float(dataIN[0].stats.npts)
            dataOUTRMS = math.sqrt(2.)*sum(np.square(dataOUT[0].data))
            dataINRMS /= float(dataOUT[0].stats.npts)  
            
            if(self.dbconn != None):
                #Write results to database      
                cur = self.dbconn.cursor()
                cur.execute("""INSERT INTO tbl_310calresults (fk_calibrationid, input_rms, output_rms, outchannel, coil_constant)
                               VALUES ("""+ "'" + str(self.cal_id) + "', '" + str(dataINRMS) + "', '" + str(dataOUTRMS) + "', '" + str(self.outChannel) + "', '" + str(dataINRMS/dataOUTRMS) + "')")
                self.dbconn.commit()
            else:
                print('input rms = ' + str(dataINRMS) + ', output rms = ' + str(dataOUTRMS) + ', coil constant = ' + str(dataINRMS/dataOUTRMS))
        except:
            self.sinecal_logger.error("Unexpected error:", sys.exc_info()[0])
            
    def computeStepCal(self):
        #cal duration needs to be divided by 10000 for step cals only.  This only applies for when you are reading the cal duration from the database. 
        if(self.dbconn != None):
            duration = self.cal_duration / 10000.0 #divide by 10000 when getting the cal_duration from the database
        else:
            duration = self.cal_duration
        
        #Determine the type of sensor from the metadata
        sensor = self.determineSensorType()
        
        #ignores every location except for Z for triaxial STS-2s
        if((self.dbconn != None) and ("Z" not in self.outChannel) and (sensor == "STS-2HG" or sensor == "STS-4B" or sensor == "STS-2")):
            print("Skipped " + str(self.outChannel) + ' ' + sensor)
            
        
        #get the poles values for the sensor type
        pz = self.pzvals(sensor)
        
        #read data for the calibration
        try:
            stOUT = Stream()   
            stime = UTCDateTime(self.startdate) - 5*60  
            stOUT = read(self.dataOutLoc,starttime = stime, endtime = stime + duration + 5*60 +900 )
            stOUT.merge()
            stIN = read(self.dataInLoc,starttime = stime, endtime = stime + duration + 5*60  + 900)
            stIN.merge()
            trIN = stIN[0]
            trOUT = stOUT[0]
            trOUT.filter('lowpass',freq=.1)
            trIN.filter('lowpass',freq=.1)
            trIN.detrend('constant')
            trIN.normalize()
            trOUT.detrend('constant')
            trOUT.normalize()
            temp=trOUT.copy()
            temp.trim(endtime = stime + int(duration/2.))
            if temp.max() < 0.0:
                trOUT.data = - trOUT.data
        except:
            if(self.dbconn != None):
                self.stepcal_logger.error('Unable to read data for {' 
                                          + 'network = ' + self.network 
                                          + ', station = ' + self.station 
                                          + ', sensor = ' + str(sensor) 
                                          + ', location = ' + str(self.location)
                                          + ', channel = ' + str(self.outChannel)
                                          + '}')
            else:
                self.stepcal_logger.error('(Manual Override) Unable read data for manual input file ' + str(self.dataInLoc) + ' and output file ' + str(self.dataOutLoc))
        try:
            f = 1. /(2*math.pi / abs(pz['poles'][0])) #compute corner (cutoff) frequency
            h = abs(pz['poles'][0].real)/abs(pz['poles'][0]) #compute damping ratio
            sen = 10.0            
            
            print ('Using: h=' + str(h) + ' f=' + str(f) + ' sen = ' + str(sen))
           
            x = numpy.array([f, h, sen])
            try:
                #compute best fit
                bf = fmin(self.resi,x, args=(trIN, trOUT),xtol=10**-8,ftol=10**-3,disp=False)
            except:
                bf = x 
   
        except:
            if(self.dbconn != None): 
                self.stepcal_logger.error('Unable to calculate {' 
                                          + 'network = ' + self.network 
                                          + ', station = ' + self.station 
                                          + ', sensor = ' + str(sensor) 
                                          + ', location = ' + str(self.location)
                                          + ', channel = ' + str(self.outChannel)
                                          + '}')
            else:
                self.stepcal_logger.error('(Manual Override) Unable to perform corner freq, damping ratio, and best fit calculations for input file ' + str(self.dataInLoc) + ' and output file ' + str(self.dataOutLoc))
        try:
            pazNOM = cornFreq2Paz(f,h)
            pazNOM['zeros']=[0.+0.j]
    
            pazPERT = cornFreq2Paz(bf[0],bf[1])
            pazPERT['zeros']=[0]
    
            trOUTsimPert = trOUT.copy()
            trOUTsimPert.simulate(paz_remove = pazPERT)
            trOUTsimPert.trim(trOUTsimPert.stats.starttime + 50,trOUTsimPert.stats.endtime - 50)
            trOUTsimPert.detrend('constant')
            trOUTsimPert.normalize()
    
            trOUTsim = trOUT.copy()
    
            trOUTsim.simulate(paz_remove = pazNOM)
            trOUTsim.trim(trOUTsim.stats.starttime + 50,trOUTsim.stats.endtime - 50)
            trOUTsim.detrend('constant')
            trOUTsim.normalize()
    
    
            trIN.trim(trIN.stats.starttime + 50,trIN.stats.endtime - 50)
            trIN.detrend('constant')
            trIN.normalize()
        
            compOUT = sum((trOUTsim.data - trIN.data)**2)
            compOUTPERT = sum((trOUTsimPert.data - trIN.data)**2)
        except:
            if(self.dbconn != None): 
                self.stepcal_logger.error('Unable to do calculation for {' 
                                          + 'network = ' + self.network 
                                          + ', station = ' + self.station 
                                          + ', sensor = ' + str(sensor) 
                                          + ', location = ' + str(self.location)
                                          + ', channel = ' + str(self.outChannel)
                                          + '}')
            else:
                self.stepcal_logger.error('(Manual Override) Unable to perform poles calculation or input file ' + str(self.dataInLoc) + ' and output file ' + str(self.dataOutLoc))
        try:
            #create a plot for the step calibration and save it to the ./temp directory.  This directory will be deleted when the program is finished running.
            plt.clf()
            t = numpy.arange(0,trOUTsim.stats.npts /trOUTsim.stats.sampling_rate,trOUTsim.stats.delta)
            plt.plot(t,trIN.data,'b',label = 'Input')
            plt.plot(t,trOUTsim.data,'k',label='h=' + str(round(h,6)) + ' f=' + str(round(f,6)) + ' resi=' + str(round(compOUT,6)))
            plt.plot(t,trOUTsimPert.data,'g',label = 'h=' + str(round(bf[1],6)) + ' f=' + str(round(bf[0],6))+ ' resi=' + str(round(compOUTPERT,6)))
            plt.xlabel('Time (s)')
            plt.ylabel('Cnts normalized')
            plt.title('Step Calibration ' + trOUT.stats.station + ' ' + str(trOUT.stats.starttime.year) + ' ' + str(trOUT.stats.starttime.julday).zfill(3))
            plt.legend(prop={'size':6})
            plt.savefig('temp/'+str(trOUT.stats.station) + str(self.outChannel) + str(self.location) + str(self.startdate.year) + str(self.julianday) + 'step.png',format = "png", dpi = 400)
        except:
            if(self.dbconn != None):
                self.stepcal_logger.error('Unable to plot {' 
                                          + 'network = ' + self.network 
                                          + ', station = ' + self.station 
                                          + ', sensor = ' + str(sensor) 
                                          + ', location = ' + str(self.location)
                                          + ', channel = ' + str(self.outChannel)
                                          + '}')
            else:
                self.stepcal_logger.error('(Manual Override) Unable to make plot for input file ' + str(self.dataInLoc) + ' and output file ' + str(self.dataOutLoc))
        if(self.dbconn != None):
            try:
                plt.close()
                #insert results into the database
                fin = open('temp/'+str(trOUT.stats.station) + str(self.outChannel) + str(self.location) + str(self.startdate.year) + str(self.julianday) + 'step.png', 'rb')
                imgdata = fin.read()
                cur = self.dbconn.cursor()
                cur.execute('''INSERT INTO tbl_300calresults (fk_calibrationid, nominal_cornerfreq, nominal_dampingratio, nominal_resi, fitted_cornerfreq, fitted_dampingratio, fitted_resi, outchannel, stepcal_img)
                            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''', [self.cal_id, round(f,6), round(h,6), round(compOUT,6), round(bf[0],6), round(bf[1],6), round(compOUTPERT,6), str(self.outChannel), psycopg2.Binary(imgdata)])
                self.dbconn.commit()
            except:
                self.stepcal_logger.error('Unable to insert into database for {' 
                                          + 'network = ' + self.network 
                                          + ', station = ' + self.station 
                                          + ', sensor = ' + str(sensor) 
                                          + ', location = ' + str(self.location)
                                          + ', channel = ' + str(self.outChannel)
                                          + '}')
                    
        else:
            try:
                print('nominal corner freq = ' + str(round(f,6)) 
                      + ', nominal damping ratio = ' + str(round(h,6)) 
                      + ', nominal best fit = ' + str(round(compOUT,6)) 
                      + ', fitted corner freq = ' + str(round(bf[0],6)) 
                      + ', fitted damping ratio = ' + str(round(bf[1], 6)) 
                      + ', pert best fit ' + str(round(compOUTPERT,6)))
                plt.show()
                plt.close()
            except:
                print('(Manual Override) Error displaying calculation results.')
    
    def computeRandomCal(self):
        if(self.dbconn != None):
            duration = self.cal_duration / 10000.0 #divide by 10000 when getting the cal_duration from the database
        else:
            duration = self.cal_duration
        
        #read data for the calibration  
        stime = UTCDateTime(self.startdate) - 5*60  
        stOUT = read(self.dataOutLoc,starttime = stime, endtime = stime + duration)
        stOUT.merge()
        stIN = read(self.dataInLoc,starttime = stime, endtime = stime + duration)
        stIN.merge()
        trIN = stIN[0]
        trOUT = stOUT[0]
        trIN.detrend('constant')
        trOUT.detrend('constant')
        temp=trOUT.copy()
        temp.trim(endtime = stime + int(duration/2.))
        if temp.max() < 0.0:
            trOUT.data = - trOUT.data
            
        samplerate = trOUT.stats.sampling_rate

        print (samplerate)

        #trIN.decimate(factor=int(samplerate), strict_length=False, no_filter=True)
        #trOUT.decimate(factor=int(samplerate), strict_length=False, no_filter=True)
        
        # If you already have the DPSS windows
        [tapers, eigen] = spectrum.dpss(len(trIN), 12, 12)
       
        x = self.pmtm(trIN.data, trIN.data, e=tapers, v=eigen, show=False)
        y = self.pmtm(trIN.data, trOUT.data, e=tapers, v=eigen, show=False)

        res = numpy.divide(y,x) 
                
                
        freq = np.multiply(np.fft.fftfreq(len(res)), samplerate)
        freq = freq[freq > 0] #only grab positive frequencies
        
        #get the index of the frequency closest to 20 seconds period (0.05 Hz)
        freq20Array = freq[(freq >= (1./20.))]
        min20Freq = np.min(freq20Array)
        print('min20Freq = ' + str(min20Freq))
        freq20Index = np.where(freq == min20Freq )[0]
        print('freq20Index = ' + str(freq20Index))

        #get the index of the frequency closest to 1000 seconds period (0.001 Hz)
        freq1000Array = freq[(freq >= (1./1000.))]
        min1000Freq = np.min(freq1000Array)
        print('min1000Freq = ' + str(min1000Freq))
        freq1000Index = np.where(freq == min1000Freq )[0]
        print('freq1000Index = ' + str(freq1000Index))
        
        freq = freq[freq1000Index : freq20Index]      
        res = res[freq1000Index : freq20Index]    
        
        res = res * (2.*math.pi*freq)
        
        #get index where frequency closest to 50 seconds (0.02 Hz)
        freq50Array = freq[(freq >= (1./50.))]
        min50Freq= np.min(freq50Array)
        print('res50Freq = ' + str(min50Freq))
        res50Index = np.where(freq == min50Freq )[0]
        print('res50Index = ' + str(res50Index))
        print('res[res50Index] = ' + str(np.abs(res[res50Index])))

        res = res / np.abs(res[res50Index]) #normalize the data
        
        for i in range(0, len(res)):
            print('at index ' + str(i) + 'res[' + str(i) + '] = ' +  str(res[i]))

        print("freq length = " + str(len(freq)))
        print("data length = " + str(len(res)))

        plt.semilogx(np.divide(1,freq), 20*np.log10(np.abs(res)))
        plt.xlabel('Period (seconds)')
        plt.ylabel('DB')
        plt.title('Instrument Response')
        plt.show()

    def pzvals(self, sensor):
        #get the instrument values for a given type of seismometer
        if sensor == 'STS-1':
            pz ={'zeros': [0.], 'poles': [-0.01234 - 0.01234j, \
                -0.01234 + 0.01234j], 'gain': 1., 'sensitivity': 1. }
        elif sensor == 'STS-2':
            pz ={'zeros': [0.], 'poles': [-0.035647 - 0.036879j, \
                -0.035647 + 0.036879j], 'gain': 1., 'sensitivity': 1. }
        elif sensor == 'STS-2HG':
            pz = {'gain': 5.96806*10**7, 'zeros': [0, 0], 'poles': [-0.035647 - 0.036879j,  
                -0.035647 + 0.036879j, -251.33, -131.04 - 467.29j, -131.04 + 467.29j],
                'sensitivity': 3.355500*10**10}
        elif sensor == 'T-120':
            pz = {'gain': 8.318710*10**17, 'zeros': [0 + 0j, 0 + 0j, -31.63 + 0j, 
                -160.0 + 0j, -350.0 + 0j, -3177.0 + 0j], 'poles':[-0.036614 + 0.037059j,  
                -0.036614 - 0.037059j, -32.55 + 0j, -142.0 + 0j, -364.0  + 404.0j, 
                -364.0 - 404.0j, -1260.0 + 0j, -4900.0 + 5204.0j, -4900.0 - 5204.0j, 
                -7100.0 + 1700.0j, -7100.0 - 1700.0j], 'sensitivity': 2.017500*10**9}
        elif sensor == 'T-240':
            pz ={'zeros': [0.], 'poles': [-0.0178231 - 0.017789j, \
                -0.0178231 + 0.017789j], 'gain': 1., 'sensitivity': 1. }
        elif sensor == 'CMG-3T':
            pz = {'gain': 5.71508*10**8, 'zeros': [0, 0], 'poles': [-0.037008 - 0.037008j,  
                -0.037008 + 0.037008j, -502.65, -1005.0, -1131.0],
                'sensitivity': 3.3554*10**10}
        elif sensor == 'KS-54000':
            pz = {'gain': 86298.5, 'zeros': [0, 0], 'poles': [-59.4313,  
                -22.7121 + 27.1065j, -22.7121 + 27.1065j, -0.0048004, -0.073199],
                'sensitivity': 3.3554*10**9}
        elif sensor == 'KS-54000':
            pz = {'gain': 86298.5, 'zeros': [0, 0], 'poles': [-59.4313,  
                -22.7121 + 27.1065j, -22.7121 + 27.1065j, -0.0048004, -0.073199],
                'sensitivity': 3.3554*10**9}
        else:
            pz = {'zeros': [-1. -1.j], 'poles': [-1. -1.j], 'gain': 1., 'sensitivity': 1.}

        return pz
    
    def resi(self, x, *args):
        f=x[0]
        h=x[1]
        sen = x[2]
        trIN = args[0]
        trOUT = args[1]
        
        paz = cornFreq2Paz(f,h)
        paz['zeros'] = [0.]
        paz['sensitivity'] = sen
        trINCP = trIN.copy()
        trINCP.trim(trINCP.stats.starttime + 50,trINCP.stats.endtime - 50)
        trINCP.detrend('constant')
        trINCP.normalize()

        trOUTsim = trOUT.copy()
        trOUTsim.simulate(paz_remove = paz)
        trOUTsim.trim(trOUTsim.stats.starttime + 50,trOUTsim.stats.endtime - 50)
        trOUTsim.detrend('constant')
        trOUTsim.normalize()

        comp = sum((trOUTsim.data - trINCP)**2)
        print(comp)
        return comp

    def determineSensorType(self):
        if(self.dbconn != None):
            #returns the sensor type for a given station location/channel
            mdgetstr = '/home/aringler/data_stuff/checkstep/./mdget.py -n ' + str(self.network) + ' -l ' + str(self.location) + ' -c ' + str(self.outChannel) + \
                        ' -s ' + str(self.station) + ' -t ' + str(self.startdate.year) + '-' + str(self.julianday) + ' -o \'instrument type\''
    
            output = commands.getstatusoutput(mdgetstr)
            
            # These might not be consistent names for sensors between networks
            try:
                output = output[1].split(',')[4] #extract the sensor data from the metadata output
            except:
                self.stepcal_logger.error('Unable to acquire sensor information for {' 
                                          + 'network = ' + self.network 
                                          + ', station = ' + self.station 
                                          + ', location = ' + str(self.location)
                                          + ', channel = ' + str(self.outChannel)
                                          + '}')
        else:
            output = self.sentype
            
        sensor = ''
        if ('T-240' in output) or ('Trillium 240' in output):
            sensor='T-240'
        elif ('T-120' in output) or ('T120' in output) or ('TRILLIUM_120' in output):
            sensor = 'T-120'
        elif ('CMG-3T' in output) or ('CMG3T' in output) or ('CMG3-T' in output):
            sensor='CMG-3T'
        elif 'STS-2HG' in output:
            sensor = 'STS2-HG'
        elif 'STS-4B' in output:
            sensor = 'STS-4B'
        elif 'KS-54000' in output:
            sensor = 'KS-54000'
        elif '151-120' in output:
            sensor = '151-120'
        elif ('STS-1' in output) or ('STS1' in output) or ('E300' in output):
            sensor = 'STS-1'
        elif ('STS-2' in output) or ('STS2' in output):
            sensor = 'STS-2'
        
        return sensor
    
    def pmtm(self, x,y, NW=None, k=None, NFFT=None, e=None, v=None, method='eigen', show=True):
        """Multitapering spectral estimation
        :param array x: the data
        :param float NW: The time half bandwidth parameter (typical values are 2.5,3,3.5,4). Must be provided otherwise the tapering windows and eigen values (outputs of dpss) must be provided  
        :param int k: uses the first k Slepian sequences. If *k* is not provided, *k* is set to *NW*2*.
        :param NW
        :param e: the matrix containing the tapering windows
        :param v: the window concentrations (eigenvalues)
        :param str method: set how the eigenvalues are used. Must be in ['unity', 'adapt', 'eigen']
        :param bool show: plot results
        """
        assert method in ['adapt','eigen','unity']
    
        N = len(x)
        # if dpss not provided, compute them
        if e is None and v is None:
            if NW is not None:
                [tapers, eigenvalues] = spectrum.dpss(N, NW, k=k)
            else:
                raise ValueError("NW must be provided (e.g. 2.5, 3, 3.5, 4")
        elif e is not None and v is not None:
            eigenvalues = v[:]
            tapers = e[:]
        else:
            raise ValueError("if e provided, v must be provided as well and viceversa.")
        nwin = len(eigenvalues) # length of the eigen values vector to be used later
    
        # set the NFFT
        if NFFT==None:
            NFFT = max(256, 2**spectrum.nextpow2(N))
        # si nfft smaller than N, cut otherwise add zero.
        # compute 
        if method == 'unity':
            weights = np.ones((nwin, 1))
        elif method == 'eigen':
            # The S_k spectrum can be weighted by the eigenvalues, as in Park et al. 
            weights = np.array([_x/float(i+1) for i,_x in enumerate(eigenvalues)])
            weights = weights.reshape(nwin,1)

        xin = np.fft.fft(np.multiply(tapers.transpose(), x), NFFT)
        yin = np.fft.fft(np.multiply(tapers.transpose(), y), NFFT)
        print('number of windows length = ' + str(len(tapers.transpose())))
        print('window length = ' + str(len(tapers.transpose()[0])))
        print('window length = ' + str(len(tapers.transpose()[11])))
        print('signal length length = ' + str(len(tapers.transpose())))
        Sk = numpy.multiply(xin,np.conj(yin))
        print('xin dimensions, width = ' + str(len(xin)) + ', height = ' + str(len(xin[0])))
        print(Sk.shape)
        Sk = np.mean(Sk * weights, axis=0)
        print(Sk.shape)
    
        #clf(); p.plot(); plot(arange(0,0.5,1./512),20*log10(res[0:256]))
        if show==True:
            spectrum.semilogy(Sk)
        return Sk
