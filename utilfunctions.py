#this is the file for the functions, shared beatween many modules

import time
import re
import hashlib
import os.path
import string
import random

import tornado.escape

import sqlalchemy.exc

import lxml
from lxml.html import builder as E
from lxml.builder import E as EE

import wand.image

import initiate
import config as cf

def get_user_permissions(user, board, action):
    if user == None:
        return False
    else:
        #if action == 'some action':
        #    get permissions from db
        #    return True/False
        return True

def generate_new_path(board, extension):
    """Here we generate pathes for saving the files ad thumbs"""
    newname = str(int(time.mktime(time.localtime()))) + ''.join(random.choice(string.digits) for _ in range(3))  + extension 
    return os.path.join('content', board, 'img', newname), newname, os.path.join('content', board, 'thumbs', 's'+newname)

def find_replacement(match):
    if match.lastgroup == 'newline':
        return (match.end()-match.start())*'<br>'
    elif match.lastgroup == 'newlineatend':
        return ''
    elif match.lastgroup == 'postlink': #make a separate handler for it
        return '<a class = "rl" href=javascript:highlight(' + match.group('postlink')[8:] + ');>'+match.group('postlink')+'</a>'

def add_markup(text):
    text = text.replace('\r\n', '\n')
    text = text.replace('\n\r', '\n')
    nl2br = re.compile('(?P<newline>\n+)(?<!$)|(?P<newlineatend>\n+)(?=$)|(?P<postlink>&gt;&gt;[0-9]+)')
    #print('==========================')
    text = nl2br.sub(find_replacement, text)
    #print('==========================')
    return text

def posting(requesth, board): #working with posted form content
    action = requesth.get_body_argument('action')
    if action == 'post':#here we act when adding a new post
        post_content = {} #will be send as kwargs to database query
        
        #theme
        theme = tornado.escape.xhtml_escape(requesth.get_body_argument('theme'))
        if len(theme) > 255:
            return 'Too long theme' #should add html escaping
        if theme != '':
            post_content['theme'] = theme
        
        #email #need to add sage option
        email = tornado.escape.xhtml_escape(requesth.get_body_argument('email'))
        if len(email) > 255:
            return 'Too long email'
        if email != '':
            post_content['email'] = email
        
        #name #here should be added tripcodes
        name = tornado.escape.xhtml_escape(requesth.get_body_argument('name'))
        if len(name) > 255:
            return 'Too long name'
        if name != '':
            post_content['name'] = name
        else:
            post_content['name'] = 'Аноним'
        
        #text of post
        text = tornado.escape.xhtml_escape(requesth.get_body_argument('text'))
        text = add_markup(text)
        if len(text) > cf.post_len:
            return 'Too long text' #should add html escaping
        elif text == '':
            return 'No text were entered'
        post_content['text'] = text
        
        #op referer
        op = requesth.get_body_argument('op')
        try: #prepairing op referer
            op = int(op)
        except ValueError:
            return 'Incorrect op referer'
        if op != 0:
            if op not in initiate.board_cache[board].posts_dict:
                return 'Thread does not exist'
            post_content['op_post'] = op

        #file management
        there_are_files = False
        if len(requesth.request.files) != 0: #would be redone for multiple file management
            there_are_files = True
            file = requesth.request.files['file1'][0]
            m = hashlib.md5()
            m.update(file['body'])
            post_content['hash1'] = m.hexdigest()
            extension = os.path.splitext(file['filename'])[1]
            path, fname, thumbpath = generate_new_path(board, extension)
            while os.path.exists(path):
                path, fname, thumbpath = generate_new_path(board, extension)
            post_content['picture'] = fname
        #that's not all, file is written after post is committed to db
        
        #adding ip
        post_content['ip'] = requesth.request.remote_ip
        
        #adding timestamp
        post_content['post_time'] = int(time.time())
        
        #preparing the post for database
            #posting should be done as a subfunction of the BOARD class
        new_post = initiate.board_cache[board].post_class(**post_content)
            #posting to the database
        initiate.sess.add(new_post)
        try:
            initiate.sess.commit()
        except sqlalchemy.exc.IntegrityError:
            initiate.sess.rollback()
            return 'This picture has already been posted'
            #adding the post to the cache
        initiate.board_cache[board].add_post(op, new_post.id)

        #adding the file and generating preview
        if there_are_files:
            with wand.image.Image(blob=file['body']) as img: #probably should add hint file format
                img.save(filename=path)
                resize_coeff = 300/max(img.width, img.height)
                if resize_coeff >= 1.0:
                    resize_coeff = 1
                img.sample(int(img.width*resize_coeff), int(img.height*resize_coeff))
                img.save(filename=thumbpath)
            
        
        #returning redirect on success
        if op == 0:
            location = '/'+board+'/'
        else:
            location = '/'+board+'/res/'+str(op)+'#'+str(new_post.id)
        requesth.set_header('Location', location)
        requesth.set_status(302)
        return 'Luckily posted'
    else:
        return 'not implemented yet'
    #

def delete_posts_by_ids(requesth, received_objects):
    board = received_objects['board']
    if board not in initiate.board_cache:
        return 'Incorrect board name'
    posts = received_objects['posts_to_del']
    #should add checking if board supports deleting
    board_post_class = initiate.board_cache[board].post_class
    if get_user_permissions(requesth.current_user, board, 'delete posts by ids'):
        posts_deleted = initiate.sess.query(board_post_class).filter(board_post_class.id.in_(posts)).delete()
    else:
        passwd = received_objects['passwd']
        posts_deleted = initiate.sess.query(board_post_class).filter(board_post_class.passwd_for_del == passwd).filter(board_post_class.id.in_(posts)).delete()
    return str(posts_deleted) + ' posts deleted!'

def get_posts_code_by_num(requesth, received_objects): #function for returning the posts code for a list of posts ids
    board = received_objects['board']
    if board not in initiate.board_cache: #all of this should be redone, it is fucking not good code
        return 'error'
    
    post_kwargs = {} #probably should be redone to make db return what we need
    if get_user_permissions(requesth.current_user, board, 'get posts code by num'):
        post_kwargs = {'ip':True}
    return_object = {} #this is what we would return
    
    in_list = set()
    for postid in received_objects['ids']:
        if type(postid) is not int:
            return 'Incorrect post ids'
        in_list.add(postid)#probably should check, if adding an already existing in set element does not cause an error
    in_list2 = list(in_list)#converting to list for _in function
    if len(in_list2) != 0:
        database_responce = initiate.sess.query(initiate.board_cache[board].post_class).filter(initiate.board_cache[board].post_class.id.in_(in_list2)).all()
        for row in database_responce:
            return_object[row.id] = row.to_dict(**post_kwargs)#probably need to be redone
            in_list.remove(row.id)
        for postid in in_list: #here we add all the posts that does not exist
            return_object[row.id] = None
    return tornado.escape.json_encode(return_object)
