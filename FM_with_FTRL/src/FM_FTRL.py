#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
@Author  : Ryan Fan 
@E-Mail  : ryanfan0528@gmail.com
@Version : v1.0
"""

import os
import re
import gc
import sys
import time
import datetime
import warnings
import commands
import numpy as np
import pandas as pd 
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

from send_email_src import send_email
from LR_FTRL import cal_loss, logistic, generator_nonzero, generate_samples, evaluate_model, get_auc


class FM:
    def __init__(self, dim, dim_lat, sigma,
                 alpha_w, alpha_v, beta_w, beta_v,
                 lambda_w1, lambda_w2, lambda_v1, lambda_v2):
        """
        the constructor of FM class
        :param dim: the dimension of feature vector
        :param dim_lat: the latent dimension of intersected feature vector
        :param sigma: the scale for the initialization of V
        :param alpha_w: the alpha parameters for learning rate in the update of weights
        :param alpha_v: the alpha parameters for learning rate in the update of V
        :param beta_w: the beta parameters for learning rate in the update of weights
        :param beta_v: the beta parameters for learning rate in the update of V
        :param lambda_w1: the L1 regularization for weights
        :param lambda_w2: the L2 regularization for weights
        :param lambda_v1: the L1 regularization for V
        :param lambda_v2: the L2 regularization for V
        """

        self.dim = dim
        self.dim_latent = dim_lat
        self.alpha_w = alpha_w
        self.alpha_v = alpha_v
        self.beta_w = beta_w
        self.beta_v = beta_v
        self.lambda_w1 = lambda_w1
        self.lambda_w2 = lambda_w2
        self.lambda_v1 = lambda_v1
        self.lambda_v2 = lambda_v2

        # initialize the zws, nws, gradient, weights
        self._zws = np.zeros(self.dim + 1)
        self._nws = np.zeros(self.dim + 1)
        self.weights = np.zeros(self.dim + 1)

        # initialize the v, zvs, nvs
        self._zvs = np.zeros((self.dim, self.dim_latent))
        self._nvs = np.zeros((self.dim, self.dim_latent))
        self.V = np.random.normal(0, sigma, size=(self.dim, self.dim_latent))

    def update_param(self, it, sample, label):
        """
        update the parameters: weights, bias, zws, nws, zvs, nvs
        :param it: stop the update of V for the first iteration
        :param sample: the feature vector                -array vector
        :param label: the ground truth label             -value
        """
        """
        个人理解：
        细读FTRL的计算流程会发现，正确的理解是这样的：
        - 最外层的loop是迭代次数；
        - 里层的第一个loop是根据过往的累积梯度，更新每个feature的权重；
        - 里层的第二个loop是根据当前的梯度，更新累积梯度，为下一次更新做准备；
        """

        # update bias
        if np.abs(self._zws[-1]) > self.lambda_w1:
            fore = (self.beta_w + np.sqrt(self._nws[-1])) / self.alpha_w + self.lambda_w2
            sign_zws = -1. if self._zws[-1] < 0. else 1.
            self.weights[-1] = -1. / fore * (self._zws[-1] - sign_zws * self.lambda_w1)
        else:
            self.weights[-1] = 0.0

        # update weights and V
        for i in generator_nonzero(sample):
            # update weights
            if np.abs(self._zws[i]) > self.lambda_w1:
                fore = (self.beta_w + np.sqrt(self._nws[i])) / self.alpha_w + self.lambda_w2
                sign_zws = -1. if self._zws[i] < 0. else 1.
                self.weights[i] = -1. / fore * (self._zws[i] - sign_zws * self.lambda_w1)
            else:
                self.weights[i] = 0.0

            # update V
            if it > 0:
                for f in range(self.dim_latent):
                    if np.abs(self._zvs[i, f]) > self.lambda_v1:
                        fore = (self.beta_v + np.sqrt(self._nvs[i, f])) / self.alpha_v + self.lambda_v2
                        sign_zvs = -1. if self._zvs[i, f] < 0. else 1.
                        self.V[i, f] = -1. / fore * (self._zvs[i, f] - sign_zvs * self.lambda_v1)
                    else:
                        self.V[i, f] = 0.0

        # predict the sample, compute the gradient
        prediction = self.predict(sample)
        base_grad = prediction - label # base_grad只有在更新bias的时候是直接使用的。

        for i in generator_nonzero(sample):
            # 为更新一阶交互weight做准备
            gradient = base_grad * sample[i]
            sigma = (np.sqrt(self._nws[i] + gradient ** 2) - np.sqrt(self._nws[i])) / self.alpha_w
            self._zws[i] += gradient - sigma * self.weights[i]
            self._nws[i] += gradient ** 2

            # 为更新二阶交互weight做准备
            for f in range(self.dim_latent):
                gradient_v = base_grad * (sample[i] * sum(self.V[:, f] * sample) - self.V[i, f] * (sample[i] ** 2))
                sigma = (np.sqrt(self._nvs[i, f] + gradient_v ** 2) - np.sqrt(self._nvs[i, f])) / self.alpha_v
                self._zvs[i, f] += gradient_v - sigma * self.V[i, f]
                self._nvs[i, f] += gradient_v ** 2

        # 为更新bias的weight做准备
        sigma = (np.sqrt(self._nws[-1] + base_grad ** 2) - np.sqrt(self._nws[-1])) / self.alpha_w
        self._zws[-1] += base_grad - sigma * self.weights[-1]
        self._nws[-1] += base_grad ** 2

        return prediction

    def predict(self, samples):
        """
        :param samples: the unseen sample  		        -array(n_samples, dimension)
        :return: prediction                             -array(n_samples, )
        """
        raw_output1 = np.dot(samples, self.weights[:-1]) + self.weights[-1]
        raw_output2 = np.square(np.dot(samples, self.V)) - np.dot(np.square(samples), np.square(self.V))
        if len(raw_output2.shape) > 1:
            raw_output2 = np.sum(raw_output2, axis=1)
        else:
            raw_output2 = sum(raw_output2)
        return logistic(raw_output1 + raw_output2/ 2)

    def train_ftrl(self, samples, labels, iteration, is_print=False):
        """
        train the LR model using the ftrl-proximal optimization algorithm
        :param samples: the feature matrix	            -n_sample * dimension
        :param labels: the label vector  	            -n_sample * 1
        :param iteration: the stooping criterion        -int
        :param is_print: whether to print               -boolean
        :return:
        """
        n_samples, dim = np.shape(samples)
        i = 0
        preds = np.zeros(n_samples)

        # 初始化time_clock
        start = time.clock()

        while i < iteration:
            log_loss = 0.0
            for t in range(n_samples):
                # retrieve the index of nonzero elements
                preds[t] = self.update_param(sample=samples[t], label=labels[t], it=i+t)
                log_loss += cal_loss(probability=preds[t], true_label=labels[t]) / n_samples
            train_error = evaluate_model(preds=preds, labels=labels)
            if i % 10 == 0 & is_print:
                # 打印训练进度
                print("FM-after iteration %s, the total logloss is %s,"
                      " the training error is %.2f%%" % (i, log_loss, train_error))

                # 计算时间统计
                train_ela = time.clock() - start
                train_ela = float(train_ela) / 60
                train_ela = round(train_ela, 2)

                # 重置clock
                start = time.clock()

                # 设置邮件发送基本信息
                receivers = ['ryanfan0313@163.com']
                Subject = 'Data Preprocessing Report'
                table_name = 'Server Is Training FM'
                date = time.strftime('%Y-%m-%d',time.localtime(time.time()))

                # 发邮件的内容
                all_final_top = pd.DataFrame({'FM iterations' : [str(i)],
                                              'Total logloss' : [str(log_loss)],
                                              'Training error': [str(train_error)]
                                            })

                # 发送邮件
                send_email_func = send_email(receivers, all_final_top, Subject, table_name, date)
                send_email_func.run()

            i += 1

    def load_model(self, file_path):
        with open(file_path, 'rb+') as f:
            s = f.read()
            model = pickle.loads(s)
            self.weights[:-1] = model['weights']
            self.weights[-1] = model['bias']
            self.V = model['V']

    def save_model(self, file_path):
        model = {"weights": self.weights[:-1], "bias": self.weights[-1], 'V': self.V}
        if not os.path.exists(file_path):
            # split the file and path
            path = file_path.split("/")[:-1]
            path = "/".join(path)
            os.makedirs(path)
        with open(file_path, 'wb+') as f:
            pickle.dump(model, f)

def main():
    # load bread_cancer datasets from sklearn
    minint = dt.load_breast_cancer()
    data_samples = minint.data
    target_samples = minint.target
    num_samples, dim_ = data_samples.shape

    # split all the samples into training data and testing data
    X_train, X_test, y_train, y_test = train_test_split(data_samples, target_samples, test_size=0.2, random_state=42)

    # define the hyper-parameter
    alpha_w_, alpha_v_, beta_w_, beta_v_ = 0.2, 0.2, 0.2, 0.2
    lambda_w1_, lambda_w2_, lambda_v1_, lambda_v2_ = 0.2, 0.2, 0.2, 0.2
    hiddens = 8
    sigma_ = 1.0
    iteration_ = 20

    # create the fm model
    fm = FM(dim=dim_, dim_lat=hiddens, sigma=sigma_,
            alpha_w=alpha_w_, alpha_v=alpha_v_, beta_w=beta_w_, beta_v=beta_v_,
            lambda_w1=lambda_w1_, lambda_w2=lambda_w2_, lambda_v1=lambda_v1_, lambda_v2=lambda_v2_)

    fm.train_ftrl(X_train, y_train, iteration_, is_print=True)
    
    # test the unseen samples
    test_preds = fm.predict(X_test)
    test_error = evaluate_model(test_preds, y_test)
    test_auc = roc_auc_score(y_true=y_test, y_score=test_preds)
    my_auc = get_auc(scores=test_preds, labels=y_test)
    print("test-error: %.2f%%" % test_error)
    print("test-sklearn auc: ", test_auc)
    print("test-my auc: ", my_auc)

    # print the parameters of trained FM model
    print("weights: ", fm.weights)
    print("bias: ", fm.weights[-1])
    print("V: ", fm.V)

if __name__ == "__main__":
    main()



