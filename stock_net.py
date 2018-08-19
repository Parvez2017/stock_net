import tensorflow as tf
import zhusuan as zs
from zhusuan import reuse
from dataset import preprocess
from tensorflow.python import debug as tf_debug
import config
import pickle
import numpy as np
import argparse
import matplotlib.pyplot as plt

def MIE(input_data,state): #GRU market information encoder
    with tf.variable_scope("GRU",reuse=tf.AUTO_REUSE):
        cell = tf.nn.rnn_cell.GRUCell(num_units = config.MIE_UNITS,name = 'GRUCELL')
        (cell_out,state) = cell(inputs = input_data,state = state)
        return state,cell_out

def q_net(input_data,y,market_encode,prev_z):
    with zs.BayesianNet() as encoder:
        cat = tf.concat([prev_z,
                        market_encode,
                        input_data,
                        y],1)
        h_z = tf.layers.dense(inputs = cat,units = config.LATENT_SIZE,activation=tf.nn.tanh,name = 'h_z',
                              reuse = tf.AUTO_REUSE)
        z_mean = tf.layers.dense(inputs = h_z,units = config.LATENT_SIZE, activation=tf.nn.tanh, name = 'z_mu',
                                 reuse=tf.AUTO_REUSE)
        z_logstd = tf.layers.dense(inputs = h_z, units = config.LATENT_SIZE, activation=tf.nn.tanh, name = 'z_delta',
                                   reuse = tf.AUTO_REUSE)
        z = zs.Normal(mean = z_mean,logstd = tf.zeros(shape=z_logstd.shape,dtype=tf.float32),group_ndims = 1,name='z',
                      reuse = tf.AUTO_REUSE)#debugging
    return z

@zs.reuse('decoder')
def p_net(observed,input_data,market_encode,prev_z,gen_mode = False):
    with zs.BayesianNet(observed=observed) as decoder:
        cat = tf.concat([prev_z,
                        market_encode,
                        input_data],1)
        h_z = tf.layers.dense(inputs=cat,units=config.LATENT_SIZE,activation = tf.nn.tanh,name='h_z_prior',
                              reuse=tf.AUTO_REUSE)
        z_mean = tf.layers.dense(inputs=h_z,units=config.LATENT_SIZE,activation = None,name='z_mu_prior',
                                 reuse = tf.AUTO_REUSE)
        z_logstd = tf.layers.dense(inputs=h_z,units=config.LATENT_SIZE,activation = None,name='z_delta_prior',
                                   reuse = tf.AUTO_REUSE)
        z = zs.Normal(name='z',mean=z_mean,logstd = z_logstd,group_ndims=2,reuse=tf.AUTO_REUSE)
        p_z = zs.Normal(name = 'pz', mean=z_mean,logstd = z_logstd,group_ndims=2,reuse=tf.AUTO_REUSE)
        if gen_mode:
            cat = tf.concat([input_data,
                            market_encode,
                            z_mean],1)
        else:#inference
            cat = tf.concat([input_data,
                            market_encode,
                            z],1)

        g = tf.layers.dense(inputs=cat, units=config.LATENT_SIZE, name='g', activation=tf.nn.tanh,reuse = tf.AUTO_REUSE)
        y = tf.layers.dense(inputs=g, units=2, activation=None,name='y_hat',reuse = tf.AUTO_REUSE)

        return y,g,p_z
def ATA(Y,G,g_t): #Attentive Temporal Auxiliary
    with tf.variable_scope("ATA", reuse=tf.AUTO_REUSE):
        Y = tf.concat([Y],0)
        Y = tf.transpose(Y,perm = [1,2,0])
        G = tf.concat([G],0)
        G = tf.transpose(G,perm = [1,0,2])
        v_i = tf.layers.dense(inputs = G,units = config.LATENT_SIZE,activation = tf.nn.tanh,use_bias=False,
                              name = 'v_i_tanh',
                              reuse=tf.AUTO_REUSE)
        v_i = tf.layers.dense(inputs = v_i,units = 1,activation=None,use_bias=False,name = 'v_i',
                              reuse=tf.AUTO_REUSE)
        v_d = tf.layers.dense(inputs = G,units = config.LATENT_SIZE,activation = tf.nn.tanh,use_bias=False,
                              name = 'v_d_tanh',
                              reuse=tf.AUTO_REUSE)
        g_t = tf.reshape(g_t,[-1,config.LATENT_SIZE,1])
        v_d = tf.matmul(v_d,g_t)
        v_star = tf.nn.softmax(tf.multiply(v_i,v_d),axis = 1)
        weighted_y = tf.matmul(Y,v_star)
        cat = tf.squeeze(tf.concat([weighted_y,g_t],axis = 1),axis = 2)
        y_T = tf.layers.dense(inputs = cat,units = 2,activation = None,name = 'classification', reuse=tf.AUTO_REUSE)
        return y_T,v_star
def inference(batch,seq_len = config.SEQ_LEN):
    state = tf.zeros(shape=[config.BATCH_SIZE, config.MIE_UNITS], name='state')
    state = tf.placeholder_with_default(state, state.shape, state.op.name)
    init_z = tf.zeros(shape=[config.BATCH_SIZE, config.LATENT_SIZE], name='prev_z')
    z = []
    z.append(init_z)
    G = []
    Y = []
    # f = []
    klds = []
    rec_losses = []
    for time_step in range(seq_len):
        state, _ = MIE(batch[:, time_step, :], state)
        # z.append(q_net(input_data=batch[:, time_step, :],
        #                market_encode=state,
        #                y=l_batch[:, time_step, :],
        #                prev_z=z[time_step]))
        y, g, p_z = p_net(observed={},
            # observed={'z': z[-1]},
            input_data=batch[:, time_step, :],
            market_encode=state,
            prev_z=z[time_step],
            gen_mode=True
        )
        z.append(p_z)
        if G.__len__() < config.SEQ_LEN - 1:
            Y.append(y)
            G.append(g)
    y, v_star = ATA(Y=Y, G=G, g_t=g)
    predict = tf.argmax(y,axis = 1)
    return predict

def train_minibatch(batch,l_batch,anneal,seq_len = config.SEQ_LEN):
    state = tf.zeros(shape=[config.BATCH_SIZE, config.MIE_UNITS], name='state')
    state = tf.placeholder_with_default(state, state.shape, state.op.name)
    init_z = tf.zeros(shape=[config.BATCH_SIZE, config.LATENT_SIZE], name='prev_z')
    z=[]
    z.append(init_z)
    G=[]
    Y=[]
    # f = []
    klds = []
    rec_losses=[]
    for time_step in range(seq_len):
        state,_ = MIE(batch[:,time_step,:],state)
        z.append(q_net(input_data=batch[:,time_step,:],
                  market_encode=state,
                  y=l_batch[:,time_step,:],
                  prev_z = z[time_step]))
        y,g,p_z = p_net(#observed={},
                        observed={'z':z[-1]},
                        input_data=batch[:,time_step,:],
                        market_encode=state,
                        prev_z=z[time_step],
                        )
        if G.__len__() < config.SEQ_LEN-1:
            Y.append(y)
            G.append(g)
        if(time_step == seq_len - 1):
            y,v_star = ATA(Y=Y,G=G,g_t=g)
        rec_loss = tf.nn.sparse_softmax_cross_entropy_with_logits(logits=y, labels=tf.argmax(l_batch[:,time_step,:],1))
        kld = -0.5 * tf.reduce_sum(
                tf.log(tf.square(z[-1].distribution.std) + 0.0001) - tf.log(tf.square(p_z.distribution.std) + 0.0001)
                - (tf.square(z[-1].distribution.std) + tf.square(z[-1].distribution.mean - p_z.distribution.mean)) / (
                            tf.square(p_z.distribution.std) + 0.0001) + 1, 1)
        rec_loss = tf.reshape(rec_loss,[config.BATCH_SIZE,1])
        kld = tf.reshape(kld,[config.BATCH_SIZE,1])
        klds.append(kld)
        rec_losses.append(rec_loss)
        # f.append(anneal*kld+rec_loss)
    klds = tf.concat([klds],axis = 0)
    klds = tf.transpose(klds,perm=[1,0,2])
    rec_losses = tf.concat([rec_losses],axis = 0)
    rec_losses = tf.transpose(rec_losses,perm = [1,0,2])
    # f = tf.concat([f],axis = 0)
    # f = tf.transpose(f,perm=[1,0,2])
    f = anneal*klds + rec_losses
    v = tf.concat([config.ALPHA*v_star,tf.ones([config.BATCH_SIZE,1,1])],1)
    loss = tf.reduce_mean(tf.multiply(v,f))
    opt = tf.train.AdamOptimizer(learning_rate= config.LR)
    optimize = opt.minimize(loss)
    return optimize,loss,v,tf.reduce_mean(klds[:,-1,:]),tf.reduce_mean(rec_losses[:,-1,:]),y


def getArgParser():
    parser = argparse.ArgumentParser(description='Train the dual-stage attention-based model on stock')
    parser.add_argument(
        '-t', '--test', action='store_true',
        help='train or test')
    return parser

if __name__ == "__main__":
    args = getArgParser().parse_args()
    test = args.test
    tf.set_random_seed(1234)
    np.random.seed(1234)

    with tf.Graph().as_default() as graph:
        batch = tf.placeholder(shape = [config.BATCH_SIZE,config.SEQ_LEN,3],dtype=tf.float32,name = 'batch')
        l_batch = tf.placeholder(shape = [config.BATCH_SIZE,config.SEQ_LEN,2],dtype=tf.float32, name = 'l_batch')
        anneal = tf.placeholder(shape = [1],dtype = tf.float32)
        optimize,loss,_,last_kl,last_rec,y= train_minibatch(batch = batch,l_batch= l_batch,anneal = anneal)
        inference = inference(batch)
        kl_sum = []
        rec_sum = []
        acc_sum = []
        saver = tf.train.Saver()
        if not test:
            f = open("dataset_train", 'rb')
            dataset = pickle.load(f)
            f = open("labelset_train", 'rb')
            labelset = pickle.load(f)
            num_iters = len(dataset) // config.BATCH_SIZE
            with tf.Session() as sess:
                sess.run(tf.global_variables_initializer())
                for e in range(config.EPOCH):
                    for i in range(num_iters):
                        feed = {batch:dataset[i*config.BATCH_SIZE:(i+1)*config.BATCH_SIZE,:,:],
                                l_batch:labelset[i*config.BATCH_SIZE:(i+1)*config.BATCH_SIZE,:,:],
                                anneal:np.array([min((num_iters*e+i)/(10000),1)])}
                        _,temploss,kl,rec_loss,y_view= sess.run([optimize,loss,last_kl,last_rec,y],feed_dict=feed)
                        # print(y)

                        print("loss",temploss,"kl:",kl,"rec:",rec_loss,"acc")
                        kl_sum.append(kl)
                        rec_sum.append(rec_loss)
                    saver.save(sess, "./models/model"+str(e)+".ckpt")
        else:
            f = open("dataset_test", 'rb')
            dataset = pickle.load(f)
            f = open("labelset_test", 'rb')
            labelset = pickle.load(f)
            num_iters = len(dataset) // config.BATCH_SIZE
            with tf.Session() as sess:
                sess.run(tf.global_variables_initializer())
                saver.restore(sess, "./models/model14.ckpt")
                correct = 0
                total = 0
                for i in range(num_iters):
                    feed = {batch:dataset[i*config.BATCH_SIZE:(i+1)*config.BATCH_SIZE,:,:]}
                    y_pred = sess.run(inference,feed_dict = feed)
                    y_gt = np.argmax(labelset[i * config.BATCH_SIZE:(i + 1) * config.BATCH_SIZE, :, :][:, -1, :],1)
                    correct += np.sum(y_gt == y_pred)
                    total += config.BATCH_SIZE
                print()
        # f = open("kl_summary",'wb')
        # pickle.dump(kl_sum,f)
        # f.close()
        # f = open("rec_sum",'wb')
        # pickle.dump(rec_sum,f)
        # f.close()
# acc = (np.argmax(y_view, 1) ==
#                            labelset[i * config.BATCH_SIZE:(i + 1) * config.BATCH_SIZE, :, :][:, -1, :].argmax(1)).sum()\
#                           / config.BATCH_SIZE