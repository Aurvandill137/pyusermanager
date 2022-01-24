# Imports
from pony.orm import *
import bcrypt


from config_class import LoginConfig
from auth_type_enum import AUTH_TYPE
from data_classes import *
from ad_config_class import AD_Config
from custom_exceptions import NotInitedException,MissingUserException,AlreadyExistsException

import ldap_stuff


def create_user(uname="",pw=None,auth=AUTH_TYPE.LOCAL):

    if not LoginConfig.inited:
        raise NotInitedException("Config not inited!")

    if not (isinstance(uname,str) or not isinstance(pw,(type(None),str)) or not isinstance(auth, AUTH_TYPE)):
        raise TypeError("supplied args do not match required types")

    if len(uname) < LoginConfig.username_min_len:
        raise ValueError("Username to short")

    if pw == None and auth != AUTH_TYPE.AD:
        raise ValueError("password empty and auth type not ad!")

    if (pw != None and auth != AUTH_TYPE.AD) and (len(pw) < LoginConfig.password_min_len):
        raise ValueError("password to short...")

    if "@" in uname and auth != AUTH_TYPE.AD:
        raise ValueError("non ad users are not allowed to have @ in their name!")


    #check if user already exists

    with db_session:
        user = User.get(username=uname)
        if user is not None:
            raise AlreadyExistsException("User already exists")
        else:
            pw_salt = None
            pw_hash = None

            if pw != None:
                pw_salt = bcrypt.gensalt(rounds=8)
                pw_hash = bcrypt.hashpw(pw.encode("utf-8"),pw_salt)

            user_to_return = User(username=uname,password_hash=pw_hash,password_salt=pw_salt,auth_type=auth)
        
    return True, user_to_return.username     
    
def login_user(uname="",pw=None):

    if not LoginConfig.inited:
        raise NotInitedException("Config not inited!")
    
    with db_session:
        #check if user exists
        requested_user = User.get(username=uname)
        if LoginConfig.debug_output:
            print(requested_user)
        if requested_user is None:
            if LoginConfig.debug_output:
                print("user not found!")
            #if we found no user lets try ad login (if its enabled!)
            if AD_Config.login and uname.endswith(AD_Config.suffix):
                if LoginConfig.debug_output:
                    print("AD Login Permitted and user is AD-User -> trying that")
                #remove ldap suffix
                uname = uname.replace(AD_Config.suffix,"")

                #create user in db if he is in necessary groups
                is_in_group = ldap_stuff.check_group(AD_Config.group,uname,pw)
                if LoginConfig.debug_output:
                    print(f"trying ad with {uname}")
                    print("check if user is in fitting group")
                    print(is_in_group)

                if is_in_group:
                    if LoginConfig.debug_output:
                        print("trying to create user!")
                    try:
                        success, created_user = create_user(f"{uname}{AD_Config.suffix}",auth=AUTH_TYPE.AD)
                        return success, created_user
                    except Exception as err:
                        if LoginConfig.debug_output:
                            print(err)
                        return False, None
                else:
                    return False, None 
                
            else:
                raise MissingUserException("No User with that Username Found")

        else:
            #if ad logins are allowed and found user auths over AD
            if requested_user.auth_type == AUTH_TYPE.AD and AD_Config.login:
                try:
                    uname = uname.replace(AD_Config.suffix,"")
                    is_in_group = ldap_stuff.check_group(AD_Config.group,uname,pw)
                    if is_in_group:
                        return True, requested_user.username
                    else:
                        return False, None
                except Exception as err:
                    return False, None
            #try normal login
            else:
                if requested_user.auth_type == AUTH_TYPE.LOCAL:
                    #check password for local user
                    salt = requested_user.password_salt
                    new_hash = bcrypt.hashpw(pw.encode("utf-8"),salt)
                    if new_hash == requested_user.password_hash:
                        return True, requested_user.username
                    else:
                        return False, None
                else:
                    raise NotImplementedError("Auth type not supported!")

            
def logout_user(token="",ip="127.0.0.1"):
    
    if not LoginConfig.inited:
        raise NotInitedException("Config not inited!")
    
    try:
        with db_session:
            found_token=Auth_Token.get(token=token)

            if found_token is None:
                return False ,"token is unknown"
            else:
                if found_token.ip == ip:
                    found_token.valid_until = "1999-01-01"
                    return True ,"token invalidated"
                else:
                    return False,"trying to logout user from different ip? wtf"
    except Exception as err:
        return False,str(err)
    
def create_token(user=None,ip="127.0.0.1",valid_days=1):
    
    if not LoginConfig.inited:
        raise NotInitedException("Config not inited!")

    if type(user) != str:
        raise ValueError("user is none")
    if type(ip) != str:
        raise ValueError("ip not a string!")
    if type(valid_days) != int or valid_days < 0:
        raise ValueError("days the token is valid does not make sense!!!")

    with db_session:
        
        found_user=User.get(username=user)
        if found_user is None:
            raise ValueError("user to create token for does not exist!")

        #generate day
        valid_until = datetime.date.today() + datetime.timedelta(days=valid_days)
        date_string = valid_until.strftime("%Y-%m-%d")

        #create token!
        token_salt = bcrypt.gensalt()
        token_to_hash = f"{user}@{ip};valid_until:{date_string}"
        token_hash = bcrypt.hashpw(token_to_hash.encode("utf-8"),token_salt)
        token_hex = token_hash.hex()
        print(f"\ngenerating token for {user}")
        print("------------------------------------------------------")
        print(f"token to hash({len(token_to_hash)}):\t{token_to_hash}")
        print(f"token salt({len(token_salt)}):\t\t{token_salt}")
        print(f"hashed token({len(token_hash)}):\t{token_hash}")
        print(f"hex token({len(token_hash)}):\t\t{token_hex}")
        print("------------------------------------------------------")    
        
        #if user exists
        token = Auth_Token.get(user=user)
        if token is None:
            #if user has no token generate new one
            token = Auth_Token(user=user,token=token_hex,ip=ip,valid_until=date_string)
        else:
            token.token=token_hex
            token.ip=ip
            token.valid_until=date_string

    return token.token
    
def verify_token(token="",ip="0.0.0.0"):
    
    if not LoginConfig.inited:
        raise NotInitedException("Config not inited!")

    return_value = (False, None,None)
    if type(token) != str or type(ip) != str:
        return_value = (False, None, None)
    else:
        with db_session:
            found_token=Auth_Token.get(token=token)
            if found_token is not None:
                user = found_token.user

                today = datetime.datetime.today()
                valid_until = datetime.datetime.strptime(user.token.valid_until,"%Y-%m-%d")

                #check token validity
                if ip == user.token.ip and token == user.token.token and today <= valid_until:
                    perm_array = []
                    for perm in user.perms:
                        perm_array.append(perm.perm_name)
                    perm_array.sort()
                    return_value = (True, perm_array, user.username)
            else:
                print("did not find token")
                return_value = (False, None, None)
    
    return return_value

def get_all_perms():

    if not LoginConfig.inited:
        raise NotInitedException("Config not inited!")

    perms = []

    with db_session:
        for perm in Permissions.select():
            perms.append(perm.perm_name)

    return perms

def assign_perms(user=None,perms=[]):

    if not LoginConfig.inited:
        raise NotInitedException("Config not inited!")

    if len(perms) == 0:
        return False, "no perms supplied!"

    if user is None or len(user) < LoginConfig.username_min_len:
        return False, "username is to Short or not given!"

    with db_session:
        user = User.get(username=user)
        if user is not None:
            msg = "added to the following classes: "
            for perm in perms:
                found_perm = Permissions.get(perm_name=perm)
                if found_perm is not None:
                    user.perms.add(found_perm)
                    msg += f" {found_perm.perm_name},"
            return True, msg
        else:
            return False, "User not found"

def does_user_exist(user=None):
    
    if not LoginConfig.inited:
        raise NotInitedException("Config not inited!")

    if user is None:
        return False
    with db_session:
        user = User.get(username=user)
        if user is None:
            #if we found no user return an empty dictionary
            return False
        else:
            return True

def get_extended_info(username):

    if not LoginConfig.inited:
        raise NotInitedException("Config not inited!")

    with db_session:
        user = User.get(username=username)

        if user is None:
            #if we found no user return an empty dictionary
            return {}, {}, {}
        
        else:
            user_dict = {}
            user_dict["username"] = user.username
            user_dict["auth_type"] = user.auth_type


            token_dict = {}
            if user.token is not None:
                token_dict["last_token_generation"] = user.token.last_login
                token_dict["valid_until"] = user.token.valid_until
                token_dict["valid_for"] = user.token.ip
                token_dict["token"] = user.token.token

            #add perms to dict!
            perm_dict = {}
            for perm in user.perms:
                perm_dict[perm.perm_name] = True

            return user_dict, token_dict, perm_dict