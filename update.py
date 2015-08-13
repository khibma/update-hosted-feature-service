# Import system modules
import urllib, urllib2, json
import sys, os
import requests
import arcpy
import ConfigParser
from xml.etree import ElementTree as ET

class AGOLHandler(object):    
    
    def __init__(self, username, password, serviceName, folderName):
        self.username = username
        self.password = password
        self.serviceName = serviceName
        self.token, self.http = self.getToken(username, password)
        self.itemID = self.findItem("Feature Service")
        self.SDitemID = self.findItem("Service Definition")
        self.folderName = folderName
        self.folderID = self.findFolder()
        
    def getToken(self, username, password, exp=60):
        
        referer = "http://www.arcgis.com/"
        query_dict = {'username': username,
                      'password': password,
                      'expiration': str(exp),
                      'client': 'referer',
                      'referer': referer,
                      'f': 'json'}   
        
        query_string = urllib.urlencode(query_dict)
        url = "https://www.arcgis.com/sharing/rest/generateToken"
        
        token = json.loads(urllib.urlopen(url + "?f=json", query_string, proxies=proxyDict).read())
        
        if "token" not in token:
            print token['error']
            sys.exit()
        else: 
            httpPrefix = "http://www.arcgis.com/sharing/rest"
            if token['ssl'] == True:
                httpPrefix = "https://www.arcgis.com/sharing/rest"
                
            return token['token'], httpPrefix
            
    def findItem(self, findType):
        #
        # Find the itemID of whats being updated
        #        
        searchURL = self.http + "/search"
        
        query_dict = {'f': 'json',
                      'token': self.token,
                      'q': "title:\""+ self.serviceName + "\"AND owner:\"" + self.username + "\" AND type:\"" + findType + "\""}    
        
        jsonResponse = sendAGOLReq(searchURL, query_dict)
        
        if jsonResponse['total'] == 0:
            print "\nCould not find a service to update. Check the service name in the settings.ini"
            sys.exit()
        else:
            print("found {} : {}").format(findType, jsonResponse['results'][0]["id"])    
        
        return jsonResponse['results'][0]["id"]

    def findFolder(self):
        #
        # Find the ID of the folder containing the service
        #

        if self.folderName == "None":
            return ""
        
        findURL = self.http + "/content/users/{}".format(self.username)

        query_dict = {'f': 'json',
                      'num': 1,
                      'token': self.token}

        jsonResponse = sendAGOLReq(findURL, query_dict)

        for folder in jsonResponse['folders']:
            if folder['title'] == self.folderName:
                return folder['id']
        
        print "\nCould not find the specified folder name provided in the settings.ini"
        print "-- If your content is in the root folder, change the folder name to 'None'"
        sys.exit()
            

def urlopen(url, data=None):
    # monkey-patch URLOPEN
    referer = "http://www.arcgis.com/"
    req = urllib2.Request(url)
    req.add_header('Referer', referer)

    if data:
        response = urllib2.urlopen(req, data)
    else:
        response = urllib2.urlopen(req)

    return response


def makeSD(MXD, serviceName, tempDir, outputSD, maxRecords):
    #
    # create a draft SD and modify the properties to overwrite an existing FS
    #    
    
    arcpy.env.overwriteOutput = True
    # All paths are built by joining names to the tempPath
    SDdraft = os.path.join(tempDir, "tempdraft.sddraft")
    newSDdraft = os.path.join(tempDir, "updatedDraft.sddraft")    
     
    arcpy.mapping.CreateMapSDDraft(MXD, SDdraft, serviceName, "MY_HOSTED_SERVICES")
    
    # Read the contents of the original SDDraft into an xml parser
    doc = ET.parse(SDdraft)  
    
    root_elem = doc.getroot()
    if root_elem.tag != "SVCManifest":
        raise ValueError("Root tag is incorrect. Is {} a .sddraft file?".format(SDDraft))
    
    # The following 6 code pieces modify the SDDraft from a new MapService
    # with caching capabilities to a FeatureService with Query,Create,
    # Update,Delete,Uploads,Editing capabilities as well as the ability to set the max
    # records on the service.
    # The first two lines (commented out) are no longer necessary as the FS
    # is now being deleted and re-published, not truly overwritten as is the 
    # case when publishing from Desktop.
    # The last three pieces change Map to Feature Service, disable caching 
    # and set appropriate capabilities. You can customize the capabilities by
    # removing items.
    # Note you cannot disable Query from a Feature Service.
    
    #doc.find("./Type").text = "esriServiceDefinitionType_Replacement" 
    #doc.find("./State").text = "esriSDState_Published"
    
    # Change service type from map service to feature service
    for config in doc.findall("./Configurations/SVCConfiguration/TypeName"):
        if config.text == "MapServer":
            config.text = "FeatureServer"
    
    #Turn off caching
    for prop in doc.findall("./Configurations/SVCConfiguration/Definition/" +
                                "ConfigurationProperties/PropertyArray/" +
                                "PropertySetProperty"):
        if prop.find("Key").text == 'isCached':
            prop.find("Value").text = "false"
        if prop.find("Key").text == 'maxRecordCount':
            prop.find("Value").text = maxRecords
    
    # Turn on feature access capabilities
    for prop in doc.findall("./Configurations/SVCConfiguration/Definition/Info/PropertyArray/PropertySetProperty"):
        if prop.find("Key").text == 'WebCapabilities':
            prop.find("Value").text = "Query,Create,Update,Delete,Uploads,Editing"

    # Add the namespaces which get stripped, back into the .SD    
    root_elem.attrib["xmlns:typens"] = 'http://www.esri.com/schemas/ArcGIS/10.1'
    root_elem.attrib["xmlns:xs"] ='http://www.w3.org/2001/XMLSchema'

    # Write the new draft to disk
    with open(newSDdraft, 'w') as f:
        doc.write(f, 'utf-8')
        
    # Analyze the service
    analysis = arcpy.mapping.AnalyzeForSD(newSDdraft)
     
    if analysis['errors'] == {}:
        # Stage the service
        arcpy.StageService_server(newSDdraft, outputSD)
        print "Created {}".format(outputSD)
            
    else:
        # If the sddraft analysis contained errors, display them and quit.
        print analysis['errors']
        sys.exit()
   
           
def upload(fileName, tags, description): 
    #
    # Overwrite the SD on AGOL with the new SD.
    # This method uses 3rd party module: requests
    #
    
    updateURL = agol.http+'/content/users/{}/{}/items/{}/update'.format(agol.username, agol.folderID, agol.SDitemID)
        
    filesUp = {"file": open(fileName, 'rb')}
    
    url = updateURL + "?f=json&token="+agol.token+ \
        "&filename="+fileName+ \
        "&type=Service Definition"\
        "&title="+agol.serviceName+ \
        "&tags="+tags+\
        "&description="+description
        
    response = requests.post(url, files=filesUp, proxies=proxyDict);     
    itemPartJSON = json.loads(response.text)
    
    if "success" in itemPartJSON:
        itemPartID = itemPartJSON['id']
        print("updated SD:   {}").format(itemPartID)
        return True
    else:
        print "\n.sd file not uploaded. Check the errors and try again.\n"  
        print itemPartJSON
        sys.exit()        
    
    
def publish():
    #
    # Publish the existing SD on AGOL (it will be turned into a Feature Service)
    #
    
    publishURL = agol.http+'/content/users/{}/publish'.format(agol.username)
    
    query_dict = {'itemID': agol.SDitemID,
              'filetype': 'serviceDefinition',
              'overwrite': 'true',
              'f': 'json',
              'token': agol.token}    
    
    jsonResponse = sendAGOLReq(publishURL, query_dict)
            
    print("successfully updated...{}...").format(jsonResponse['services'])
    
    return jsonResponse['services'][0]['serviceItemId']
    

def enableSharing(newItemID, everyone, orgs, groups):
    #
    # Share an item with everyone, the organization and/or groups
    #

    shareURL = agol.http+'/content/users/{}/{}/items/{}/share'.format(agol.username, agol.folderID, newItemID)

    if groups == None:
        groups = ''
    
    query_dict = {'f': 'json',
                  'everyone' : everyone,
                  'org' : orgs,
                  'groups' : groups,
                  'token': agol.token}    
    
    jsonResponse = sendAGOLReq(shareURL, query_dict)
    
    print("successfully shared...{}...").format(jsonResponse['itemId'])    
    
    
    
def sendAGOLReq(URL, query_dict):
    #
    # Helper function which takes a URL and a dictionary and sends the request
    #
    
    query_string = urllib.urlencode(query_dict)    
    
    jsonResponse = urllib.urlopen(URL, urllib.urlencode(query_dict), proxies=proxyDict)
    jsonOuput = json.loads(jsonResponse.read())
    
    wordTest = ["success", "results", "services", "notSharedWith", "folders"]
    if any(word in jsonOuput for word in wordTest):
        return jsonOuput    
    else:
        print "\nfailed:"
        print jsonOuput
        sys.exit()
        
    
if __name__ == "__main__":
    #
    # start
    #
    
    print "Starting Feature Service publish process"
    
    # Find and gather settings from the ini file
    localPath = sys.path[0]
    settingsFile = os.path.join(localPath, "settings.ini")

    if os.path.isfile(settingsFile):
        config = ConfigParser.ConfigParser()
        config.read(settingsFile)
    else:
        print "INI file not found. \nMake sure a valid 'settings.ini' file exists in the same directory as this script."
        sys.exit()
    
    # AGOL Credentials
    inputUsername = config.get( 'AGOL', 'USER')
    inputPswd = config.get('AGOL', 'PASS')

    # FS values
    MXD = config.get('FS_INFO', 'MXD')
    serviceName = config.get('FS_INFO', 'SERVICENAME')   
    folderName = config.get('FS_INFO', 'FOLDERNAME')
    tags = config.get('FS_INFO', 'TAGS')
    description = config.get('FS_INFO', 'DESCRIPTION')
    maxRecords = config.get('FS_INFO', 'MAXRECORDS')
    
    # Share FS to: everyone, org, groups
    shared = config.get('FS_SHARE', 'SHARE')
    everyone = config.get('FS_SHARE', 'EVERYONE')
    orgs = config.get('FS_SHARE', 'ORG')
    groups = config.get('FS_SHARE', 'GROUPS')  #Groups are by ID. Multiple groups comma separated
    
    pxy_srvr = config.get('PROXY', 'SERVER')
    pxy_port = config.get('PROXY', 'PORT')
    pxy_user = config.get('PROXY', 'USER')
    pxy_pass = config.get('PROXY', 'PASS')

    http_proxy  = "http://" + pxy_user + ":" + pxy_pass + "@" + pxy_srvr + ":" + pxy_port
    https_proxy = "http://" + pxy_user + ":" + pxy_pass + "@" + pxy_srvr + ":" + pxy_port
    ftp_proxy   = "http://" + pxy_user + ":" + pxy_pass + "@" + pxy_srvr + ":" + pxy_port
    proxyDict = {"http"  : http_proxy, "https":https_proxy,"ftp": ftp_proxy}

    
    # create a temp directory under the script     
    tempDir = os.path.join(localPath, "tempDir")
    if not os.path.isdir(tempDir):
        os.mkdir(tempDir)  
    finalSD = os.path.join(tempDir, serviceName + ".sd")  

    #initialize AGOLHandler class
    agol = AGOLHandler(inputUsername, inputPswd, serviceName, folderName)
    
    # Turn map document into .SD file for uploading
    makeSD(MXD, serviceName, tempDir, finalSD, maxRecords)
    
    # overwrite the existing .SD on arcgis.com
    
    if upload(finalSD, tags, description):
        
        # publish the sd which was just uploaded
        newItemID = publish()
        
        # share the item
        if shared:
            enableSharing(newItemID, everyone, orgs, groups)
            
        print "\nfinished."
    
