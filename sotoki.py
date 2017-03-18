#!/usr/bin/env python2
# -*-coding:utf8 -*
"""sotoki.

Usage:
  sotoki.py run <url> <publisher> [--directory=<dir>] [--nozim] [--tag-depth=<tag_depth>]
  sotoki.py (-h | --help)
  sotoki.py --version

Options:
  -h --help     Show this screen.
  --version     Show version.
  --directory=<dir>   Specify a directory for xml files [default: work/dump/]
  --nozim       doesn't make zim file, output will be in work/output/ in normal html (otherwise work/ouput/ will be in deflate form and will produice a zim file)
  --tag-depth=<tag_depth>   Specify number of question, order by Score, to show in tags pages (should be a multiple of 100, default all question are in tags pages) [default: -1]

"""
import sys
import datetime
import subprocess
import time
import shutil
import os
import re
import os.path
from distutils.dir_util import copy_tree

#from subprocess32 import check_output
#from subprocess32 import TimeoutExpired
from subprocess32 import call
import shlex

from multiprocessing import Pool
from multiprocessing import cpu_count
from multiprocessing import Queue
from multiprocessing import Process

import envoy
import logging
import sqlite3

from xml.sax import make_parser, handler

from hashlib import sha256
from urllib2 import urlopen

from jinja2 import Environment
from jinja2 import FileSystemLoader
import bs4 as BeautifulSoup

from lxml.etree import parse as string2xml
from lxml.html import fromstring as string2html
from lxml.html import tostring as html2string
from docopt import docopt
from slugify import slugify
from markdown import markdown as md
import pydenticon
from string import punctuation
import zlib

from PIL import Image
from resizeimage import resizeimage
import magic

from itertools import chain


#########################
#        Question       #
#########################
class QuestionRender(handler.ContentHandler):

    def __init__(self, templates, database, output, title, publisher, dump, cores, cursor,conn, deflate,site_url, redirect_file):
        self.cursor=cursor
        self.conn=conn
        self.site_url=site_url
        self.post={}
        self.comments=[]
        self.answers=[]
        self.whatwedo="post"
        self.nb=0 #Nomber of post generate
        os.makedirs(os.path.join(output, 'question'))
        self.request_queue = Queue(cores*2)
        self.workers = []
        self.cores=cores
        self.conn=conn
        for i in range(self.cores): 
            self.workers.append(Worker(self.request_queue))
        for i in self.workers:
            i.start()
        self.f_redirect = open(redirect_file, "w")

    def startElement(self, name, attrs): #For each element
        if name == "comments" and self.whatwedo == "post": #We match if it's a comment of post
            self.whatwedo="post/comments"
            self.comments=[]
            return
        if name == "comments" and self.whatwedo == "post/answers": #comment of answer
            self.whatwedo="post/answers/comments"
            self.comments=[]
            return
        if name == "answers": #a answer
            self.whatwedo="post/answers"
            self.comments=[]
            self.answers=[]
            return
        if name== 'row': #Here is a answer
            tmp={}
            for k in attrs.keys(): #Get all item
                tmp[k] = attrs[k]
            tmp["Score"] = int(tmp["Score"])
            if self.post.has_key("AcceptedAnswerId") and self.post["AcceptedAnswerId"] == tmp["Id"]:
                tmp["Accepted"] = True
            else:
                tmp["Accepted"] = False

            if tmp.has_key("OwnerUserId"): #We put the good name of the user how made the post
                user=cursor.execute("SELECT * FROM users WHERE id = ?", (int(tmp["OwnerUserId"]),)).fetchone()
                if user != None:
                    tmp["OwnerUserId"] =  dict_to_unicodedict(user)
                else:
                    tmp["OwnerUserId"] =  dict_to_unicodedict({ "DisplayName" : u"None" })
            elif tmp.has_key("OwnerDisplayName"):
                tmp["OwnerUserId"] = dict_to_unicodedict({ "DisplayName" : tmp["OwnerDisplayName"] })
            else:
                tmp["OwnerUserId"] =  dict_to_unicodedict({ "DisplayName" : u"None" })
            #print "        new answers"
            self.answers.append(tmp)
            return

        if name == "comment": #Here is a comments
            tmp={}
            for k in attrs.keys(): #Get all item
                tmp[k] = attrs[k]
            #print "                 new comments"
            if tmp.has_key("UserId"): #We put the good name of the user how made the comment
                user=cursor.execute("SELECT * FROM users WHERE id = ?", (int(tmp["UserId"]),)).fetchone()
                if tmp.has_key("UserId") and  user != None :
                    tmp["UserDisplayName"] = dict_to_unicodedict(user)["DisplayName"]
                else:
                    tmp["UserDisplayName"] = u"None"
            else:
                tmp["UserDisplayName"] = u"None"

            if tmp.has_key("Score"):
                tmp["Score"] = int(tmp["Score"])
            self.comments.append(tmp)
            return

        if name == "link": #We add link
            if attrs["LinkTypeId"] == "1":
                self.post["relateds"].append(attrs["PostId"])
            elif attrs["LinkTypeId"] == "3":
                self.post["duplicate"].append(attrs["PostId"])
            return

        if name != 'post': #We go out if it's not a post, we because we have see all name of posible tag (answers, row,comments,comment and we will see after post) This normally match only this root
            print "nothing " + name
            return

        if name == 'post': #Here is a post
            self.whatwedo = "post"
            for k in attrs.keys(): #get all item
                self.post[k] = attrs[k]
            self.post["relateds"] = [] #Prepare list for relateds question
            self.post["duplicate"] = [] #Prepare list for duplicate question
            self.post["slugify_name"] = slugify(self.post["Title"])[:248]
            self.post["filename"] = '%s.html' % self.post["slugify_name"]

            if self.post.has_key("OwnerUserId"):#We put the good name of the user how made the post
                user=cursor.execute("SELECT * FROM users WHERE id = ?", (int(self.post["OwnerUserId"]),)).fetchone()
                if user != None:
                    self.post["OwnerUserId"] =  dict_to_unicodedict(user)
                else:
                    self.post["OwnerUserId"] =  dict_to_unicodedict({ "DisplayName" : u"None" })
            elif self.post.has_key("OwnerDisplayName"):
                self.post["OwnerUserId"] = dict_to_unicodedict({ "DisplayName" : self.post["OwnerDisplayName"] })
            else:
                self.post["OwnerUserId"] =  dict_to_unicodedict({ "DisplayName" : u"None" })

    def endElement(self, name):
        if self.whatwedo=="post/answers/comments": #If we have a post with answer and comment on this answer, we put comment into the anwer
            self.answers[-1]["comments"] = self.comments
            self.whatwedo="post/answers"
        if self.whatwedo=="post/answers": #If we have a post with answer(s), we put answer(s) we put them into post
            self.post["answers"] = self.answers
        elif self.whatwedo=="post/comments": #If we have post without answer but with comments we put comment into post
            self.post["comments"] = self.comments

        if name == "post":
            #print self.post
            self.nb+=1 
            if self.nb % 1000 == 0:
                print "Already " + str(self.nb) + " questions done!"
                self.conn.commit()
            self.post["Tags"] = self.post["Tags"][1:-1].split('><')
            for t in self.post["Tags"]: #We put tags into db
                sql = "INSERT INTO QuestionTag(Score, Title, CreationDate, Tag) VALUES(?, ?, ?, ?)"
                self.cursor.execute(sql, (self.post["Score"], self.post["Title"], self.post["CreationDate"], t))
            #Make redirection 
            for ans in self.answers:
                self.f_redirect.write("a/" + str(ans["Id"]) + ",Answer " + str(ans["Id"]) + ",question/" + self.post["filename"] + "#a" + str(ans["Id"]) + "\n")
            self.f_redirect.write("q/" + str(self.post["Id"]) +",Question " + str(self.post["Id"]) + ",question/" + self.post["filename"] + "\n")
            data_send = [ some_questions, templates, output, title, publisher, self.post, "question.html", deflate, self.site_url ]
            self.request_queue.put(data_send)
            #some_questions(templates, output, title, publisher, self.post, "question.html", self.cursor)
            #Reset element
            self.post={}
            self.comments=[]
            self.answers=[]


    def endDocument(self):
        print "---END--"
        self.conn.commit()
        #closing thread
        for i in range(self.cores):
            self.request_queue.put(None)
        for i in self.workers:
            i.join()
        self.f_redirect.close()

def some_questions(templates, output, title, publisher, question, template_name, deflate,site_url):
    try:
        question["Score"] = int(question["Score"])
        if question.has_key("answers"):
            question["answers"] = sorted(question["answers"], key=lambda k: k['Score'],reverse=True) 
            question["answers"] = sorted(question["answers"], key=lambda k: k['Accepted'],reverse=True) #sorted is stable so accepted will be always first, then other question will be sort in ascending order
            for ans in question["answers"]:
                ans["Body"]=image(ans["Body"],output)
            if question.has_key("links"):
                for link in question["links"]:
                    link=slugify(link)[:248]
            if question.has_key("relateds"):
                for related in question["relateds"]:
                    related=slugify(related)[:248]

        if question["slugify_name"] != "":
            filepath = os.path.join(output, 'question', question["filename"])
            question["Body"] = image(question["Body"],output)
            try:
                jinja(
                    filepath,
                    template_name,
                    templates,
                    False,
                    deflate,
                    question=question,
                    rooturl="..",
                    title=title,
                    publisher=publisher,
                    site_url=site_url,
                    )
            except Exception, e:
                print ' * failed to generate: %s' % filename
                print "erreur jinja" + str(e)
                print question
        else: #Sometime (when title only have caratere that we can't sluglify) 
                print "erreur avec le titre" #lever une exception ?
    except Exception, e:
        print "Erreur with one post : " + str(e)


#########################
#        Tags           #
#########################

class TagsRender(handler.ContentHandler):

    def __init__(self, templates, database, output, title, publisher, dump, cursor, conn, deflate, tag_depth):
        # index page
        self.tags = []

    def startElement(self, name, attrs): #For each element
        if name == "row": #If it's a tag (row in tags.xml)
            self.tags.append({'TagName': attrs["TagName"]})

    def endDocument(self):
        jinja(
            os.path.join(output, 'index.html'),
            'tags.html',
            templates,
            False,
            deflate,
            tags=self.tags,
            rooturl=".",
            title=title,
            publisher=publisher,
        )
        # tag page
        print "Render tag page"
        list_tag = map(lambda d: d['TagName'], self.tags)
        os.makedirs(os.path.join(output, 'tag'))
        for tag in list(set(list_tag)):
            dirpath = os.path.join(output, 'tag')
            tagpath = os.path.join(dirpath, '%s' % tag)
            os.makedirs(tagpath)
            print tagpath
            # build page using pagination
            if tag_depth==-1:
                offset = 0
                page = 1
                while offset is not None:
                    fullpath = os.path.join(tagpath, '%s.html' % page)
                    questions = cursor.execute("SELECT * FROM questiontag WHERE Tag = ? LIMIT 101 OFFSET ? ", (str(tag), offset,)).fetchall()
                    try:
                        questions[100]
                    except IndexError:
                        offset = None
                    else:
                        offset += 100
                    questions = questions[:100]
                    for question in questions:
                        question["filepath"] = slugify(question["Title"])[:248]
                    jinja(
                        fullpath,
                        'tag.html',
                        templates,
                        False,
                        deflate,
                        tag=tag,
                        index=page,
                        questions=questions,
                        rooturl="../..",
                        hasnext=bool(offset),
                        next=page + 1,
                        title=title,
                        publisher=publisher,
                    )
                    page += 1
            else:
                offset = 0
                page = 1
                while offset is not None and offset < tag_depth:
                    fullpath = os.path.join(tagpath, '%s.html' % page)
                    questions = cursor.execute("SELECT * FROM questiontag WHERE Tag = ? ORDER BY Score DESC LIMIT 101 OFFSET ? ", (str(tag), offset,)).fetchall()
                    try:
                        questions[100]
                    except IndexError:
                        offset = None
                    else:
                        offset += 100
                    if offset > tag_depth:
                        offset = None
                    questions = questions[:100]
                    for question in questions:
                        question["filepath"] = slugify(question["Title"])[:248]
                    jinja(
                        fullpath,
                        'tag.html',
                        templates,
                        False,
                        deflate,
                        tag=tag,
                        index=page,
                        questions=questions,
                        rooturl="../..",
                        hasnext=bool(offset),
                        next=page + 1,
                        title=title,
                        publisher=publisher,
                    )
                    page += 1


#########################
#        Users          #
#########################
class UsersRender(handler.ContentHandler):

    def __init__(self, templates, database, output, title, publisher, dump, cores, cursor, deflate, site_url):
        self.identicon_path = os.path.join(output, 'static', 'identicon')
        self.id=0
        self.site_url=site_url
        os.makedirs(self.identicon_path)
        os.makedirs(os.path.join(output, 'user'))
        # Set-up a list of foreground colours (taken from Sigil).
        self.foreground = [
            "rgb(45,79,255)",
            "rgb(254,180,44)",
            "rgb(226,121,234)",
            "rgb(30,179,253)",
            "rgb(232,77,65)",
            "rgb(49,203,115)",
            "rgb(141,69,170)"
            ]
        # Set-up a background colour (taken from Sigil).
        self.background = "rgb(224,224,224)"

        # Instantiate a generator that will create 5x5 block identicons
        # using SHA256 digest.
        self.generator = pydenticon.Generator(5, 5, foreground=self.foreground, background=self.background)  # noqa
        self.request_queue = Queue(cores*2)
        self.workers = []
        self.cores=cores
        self.conn=conn
        for i in range(self.cores): 
            self.workers.append(Worker(self.request_queue))
        for i in self.workers:
            i.start()

    def startElement(self, name, attrs): #For each element
        if name != "row": #If it's not a user (row in users.xml) we pass
            return
        self.id +=1
        if self.id % 1000 == 0:
            print "Already " + str(self.id) + " Users done !"
            self.conn.commit()
        user={}
        for k in attrs.keys(): #get all item
            user[k] = attrs[k]
        if user != {}:
            sql = "INSERT INTO users(id, DisplayName, Reputation) VALUES(?, ?, ?)"
            cursor.execute(sql, (int(user["Id"]),  user["DisplayName"], user["Reputation"]))

            data_send = [some_user, user, self.generator, templates, output, publisher, self.site_url]
            self.request_queue.put(data_send)

    def endDocument(self):
        print "---END--"
        self.conn.commit()
        #closing thread
        for i in range(self.cores):
            self.request_queue.put(None)
        for i in self.workers:
            i.join()

def some_user(user,generator,templates, output, publisher, site_url):
    username = slugify(user["DisplayName"])
    filename = username + '.png'
    fullpath = os.path.join(output, 'static', 'identicon', filename)
    try:
        url=user["ProfileImageUrl"]
        download(url, fullpath)
    except Exception,e:
        # Generate big identicon
        padding = (20, 20, 20, 20)
        identicon = generator.generate(username, 128, 128, padding=padding, output_format="png")  # noqa
        with open(fullpath, "wb") as f:
            f.write(identicon)

    # generate user profile page
    filename = '%s.html' % username
    fullpath = os.path.join(output, 'user', filename)
    jinja(
        fullpath,
        'user.html',
        templates,
        False,
        deflate,
        user=user,
        title=title,
        rooturl="..",
        publisher=publisher,
        site_url=site_url,
    )

#########################
#        Tools          #
#########################


class Worker(Process):
    def __init__(self, queue):
        super(Worker, self).__init__()
        self.queue = queue

    def run(self):
        for data in iter(self.queue.get, None):
            try:
                data[0](*data[1:])
                #some_questions(*data)
            except Exception as exc:
                print 'error while rendering question:', data[-1]['Id']
                print exc

def intspace(value):
    orig = str(value)
    new = re.sub("^(-?\d+)(\d{3})", '\g<1> \g<2>', orig)
    if orig == new:
        return new
    else:
        return intspace(new)


def markdown(text):
    # FIXME: add postprocess step to transform 'http://' into a link
    # strip p tags
    return md(text)[3:-4]


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def scale(number):
    """Convert number to scale to be used in style to color arrows
    and comment score"""
    if number < 0:
        return 'negative'
    if number == 0:
        return 'zero'
    if number < 3:
        return 'positive'
    if number < 8:
        return 'good'
    return 'verygood'

ENV = None  # Jinja environment singleton

def jinja(output, template, templates, raw, deflate, **context):
    template = ENV.get_template(template)
    page = template.render(**context)
    if raw:
        page = "{% raw %}" + page + "{% endraw %}"
    with open(output, 'w') as f:
        if deflate:
            f.write(zlib.compress(page.encode('utf-8')))
        else:
             f.write(page.encode('utf-8'))

def jinja_init(templates):
    global ENV
    templates = os.path.abspath(templates)
    ENV = Environment(loader=FileSystemLoader((templates,)))
    filters = dict(
        markdown=markdown,
        intspace=intspace,
        scale=scale,
        clean=lambda y: filter(lambda x: x not in punctuation, y),
        slugify=slugify,
    )
    ENV.filters.update(filters)


def download(url, output):
    if url[0:2] == "//":
        url="http:"+url
    response = urlopen(url)
    output_content = response.read()
    with open(output, 'w') as f:
        f.write(output_content)
    return response.headers

def get_filetype(headers,path):
    type="none"
    if headers.has_key('content-type'):
        if ("png" in headers['content-type']) or ("PNG" in headers['content-type']):
            type="png"
        elif ("jpg" in headers['content-type']) or ("jpeg" in headers['content-type']) or ("JPG" in headers['content-type']) or ("JPEG" in headers['content-type']):
            type="jpeg"
        elif ("gif" in headers['content-type']) or ("GIF" in headers['content-type']):
            type="gif"
    else:
        with magic.Magic() as m:
            mine=m.id_filename(path)
            if "PNG" in mine:
                type="png"
            elif "JPEG" in mine:
                type="jpeg"
            elif "GIF" in mine:
                type="gif"
    return type

def image(text_post, output):
    images = os.path.join(output, 'static', 'images')
    body = string2html(text_post)
    imgs = body.xpath('//img')
    for img in imgs:
            src = img.attrib['src']
            ext = os.path.splitext(src)[1]
            filename = sha256(src).hexdigest() + ext
            out = os.path.join(images, filename)
            # download the image only if it's not already downloaded
            if not os.path.exists(out) : 
                try:
                    headers=download(src, out)
                    type=get_filetype(headers,out)
                    # update post's html
                    src = '../static/images/' + filename
                    resize_one(out,type)
                    optimize_one(out,type)
                    img.attrib['src'] = src
                except Exception,e:
                    # do nothing
                    print e
                    pass
                img.attrib['style']= "max-width:100%"
                # finalize offlining

    # does the post contain images? if so, we surely modified
    # its content so save it.
    if imgs:
        text_post = html2string(body)
    return text_post

def grab_title_description_favicon(url, output_dir):
    output = urlopen(url).read()
    soup = BeautifulSoup.BeautifulSoup(output, 'html.parser')
    title = soup.find('meta', attrs={"name": u"twitter:title"})['content']
    description = soup.find('meta', attrs={"name": u"twitter:description"})['content']
    favicon = soup.find('link', attrs={"rel": u"image_src"})['href']
    if favicon[:2] == "//":
        favicon = "http:" + favicon
    favicon_out = os.path.join(output_dir, 'favicon.png')
    download(favicon, favicon_out)
    resize_image_profile(favicon_out)
    return [title, description]


def resize_image_profile(image_path):
    image = Image.open(image_path)
    w, h = image.size
    image = image.resize((48, 48), Image.ANTIALIAS)
    image.save(image_path)

def exec_cmd(cmd, timeout=None):
    try:
        #return check_output(shlex.split(cmd), timeout=timeout)
        return call(shlex.split(cmd), timeout=timeout)
    except Exception, e:
        print e
        pass
def bin_is_present(binary):
    try:
        subprocess.Popen(binary,
                         universal_newlines=True,
                         shell=False,
                         stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,
                         bufsize=0)
    except OSError:
        return False
    else:
        return True

def dict_to_unicodedict(dictionnary):
    dict_ = {}
    if dictionnary.has_key("OwnerDisplayName"):
        dictionnary["OwnerDisplayName"] = u""
    for k, v in dictionnary.items():
        if isinstance(k, str):
            unicode_key = k.decode('utf8')
        else:
            unicode_key = k
        if isinstance(v, unicode) or type(v) == type({}) or type(v) == type(1):
            unicode_value = v
        else:
            unicode_value =  v.decode('utf8')
        dict_[unicode_key] = unicode_value

    return dict_

def prepare(dump_path):
    cmd="bash prepare_xml.sh " + dump_path
    if exec_cmd(cmd) == 0:
        print "Prepare xml ok"
    else:
        sys.exit("Unable to prepare xml :(")

def optimize_one(path,type):
    if type == "jpeg":
        exec_cmd("jpegoptim --strip-all -m50 " + path, timeout=10)
    elif type == "png" :
        exec_cmd("pngquant --verbose --nofs --force --ext=.png " + path, timeout=10)
        exec_cmd("advdef -q -z -4 -i 5  " + path, timeout=10)
    elif type == "gif":
        exec_cmd("gifsicle --batch -O3 -i " + path, timeout=10)

def resize_one(path,type):
    if type in ["gif", "png", "jpeg"]:
        exec_cmd("mogrify -resize 540x\> " + path, timeout=10)

#########################
#     Zim generation    #
#########################

def create_zims(title, publisher, description,redirect_file):
    print 'Creating ZIM files'
    # Check, if the folder exists. Create it, if it doesn't.
    lang_input = "en"
    html_dir = os.path.join("work", "output")
    zim_path = dict(
        title=title.lower(),
        lang=lang_input,
        date=datetime.datetime.now().strftime('%Y-%m')
    )
#    zim_path = "work/", "{title}_{lang}_all_{date}.zim".format(**zim_path)
    zim_path = os.path.join("work/", "{title}_{lang}_all_{date}.zim".format(**zim_path))

    title = title.replace("-", " ")
    creator = title
    return create_zim(html_dir, zim_path, title, description, lang_input, publisher, creator,redirect_file)


def create_zim(static_folder, zim_path, title, description, lang_input, publisher, creator,redirect_file):
    print "\tWritting ZIM for {}".format(title)
    context = {
        'languages': lang_input,
        'title': title,
        'description': description,
        'creator': creator,
        'publisher': publisher,
        'home': 'index.html',
        'favicon': 'favicon.png',
        'static': static_folder,
        'zim': zim_path,
        'redirect_csv' : redirect_file
    }

    cmd = ('zimwriterfs --inflateHtml --redirects="{redirect_csv}" --welcome="{home}" --favicon="{favicon}" '
           '--language="{languages}" --title="{title}" '
           '--description="{description}" '
           '--creator="{creator}" --publisher="{publisher}" "{static}" "{zim}"'
           .format(**context))
    print cmd

    if exec_cmd(cmd) == 0:
        print "Successfuly created ZIM file at {}".format(zim_path)
        return True
    else:
        print "Unable to create ZIM file :("
        return False


if __name__ == '__main__':
    arguments = docopt(__doc__, version='sotoki 0.1')
    if arguments['run']:
        if not arguments['--nozim'] and not bin_is_present("zimwriterfs"):
            sys.exit("zimwriterfs is not available, please install it.")
        tag_depth = int(arguments['--tag-depth'])
        if tag_depth != -1 and tag_depth <= 0:
            sys.exit("--tag-depth should be a positive integer")
        url = arguments['<url>']
        publisher = arguments['<publisher>']
        dump = arguments['--directory']
        deflate = not arguments['--nozim']
        database = 'work'
        # render templates into `output`
        #templates = 'templates'
        templates = 'templates_mini'
        output = os.path.join('work', 'output')
        os.makedirs(output)
        os.makedirs(os.path.join(output, 'static', 'images'))
        cores = cpu_count() / 2 or 1

        #prepare db
        db = os.path.join(database, 'se-dump.db')
        conn = sqlite3.connect(db) #can be :memory: for small dump  
        conn.row_factory = dict_factory
        cursor = conn.cursor()
        # create table tags-questions
        sql = "CREATE TABLE IF NOT EXISTS questiontag(id INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, Score INTEGER, Title TEXT, CreationDate TEXT, Tag TEXT)"
        cursor.execute(sql)
        #creater user table
        sql = "CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY UNIQUE, DisplayName TEXT, Reputation TEXT)"
        cursor.execute(sql)
        #create table for links
        sql = "CREATE TABLE IF NOT EXISTS links(id INTEGER, title TEXT)"
        cursor.execute(sql)
        conn.commit()
        redirect_file = os.path.join('work', 'redirection.csv')

        prepare(dump)
        title, description = grab_title_description_favicon(url, output)
        jinja_init(templates)

        #Generate users !
        parser = make_parser()
        parser.setContentHandler(UsersRender(templates, database, output, title, publisher, dump, cores, cursor, deflate,url))
        parser.parse(os.path.join(dump, "users.xml"))
        conn.commit()


        #Generate question !
        parser = make_parser()
        parser.setContentHandler(QuestionRender(templates, database, output, title, publisher, dump, cores, cursor,conn, deflate,url,redirect_file))
        parser.parse(os.path.join(dump, "prepare.xml"))
        conn.commit()

        #Generate tags !
        parser = make_parser()
        parser.setContentHandler(TagsRender(templates, database, output, title, publisher, dump, cores, cursor, deflate,tag_depth))
        parser.parse(os.path.join(dump, "tags.xml"))

        conn.close()
        # copy static
        copy_tree('static', os.path.join(output, 'static'))
        if not arguments['--nozim']:
            done=create_zims(title, publisher, description, redirect_file)
            if done == True:
                print "remove " + output
                shutil.rmtree(output)
                print "remove " + db
                os.remove(db)
                os.remove(redirect_file)
