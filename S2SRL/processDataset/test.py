#-*-coding:utf-8-*-
"""
This file is used to test certain functions or methods.
"""
def test():
    a = [2 - 1.5] * 10
    print (a)

    actions_t = slice(3)
    log_prob_v = [[1,2,3],[4,5,6],[7,8,9]]
    a = log_prob_v[actions_t]
    print(a)

    answer = 111
    orig_response = 222
    print (str(answer) + "::" + str(orig_response))
    print ("%s::%s" %(answer, orig_response))
    temp_string = "%s::%s" %(answer, orig_response)
    print (temp_string)

    line = '''symbolic_seq.append({"A1": ['Q910670', 'P47', 'Q20667921']})'''
    key = line[line.find("{") + 1:line.find('}')].split(':')[0].replace('\"', '').strip()
    print (key)
    val = line[line.find("{") + 1: line.find('}')].split(':')[1].strip()
    print (val)
    val = val.replace('[', '')
    print(val)
    val = val.replace(']', '')
    print(val)
    val = val.replace("\'", "")
    print(val)
    val = val.strip()
    print(val)
    val =val.split(',')
    print(val)

    temp_list = list()
    temp_dict = {"a":1}
    if 'a' in temp_dict:
        print ("a")
    if '1' in temp_dict:
        print("1")
    temp_list.append(temp_dict)
    temp_list.append(False)
    print (temp_list)
    for item in temp_list:
        print (item.__class__)


if __name__ == "__main__":
    test()