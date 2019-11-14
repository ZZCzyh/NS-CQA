import torch
from torch.nn.utils.convert_parameters import (vector_to_parameters,
                                               parameters_to_vector)
from . import data, model, utils, retriever
import torch.optim as optim
import torch.nn.functional as F
import random
import logging
log = logging.getLogger("MetaLearner")

class MetaLearner(object):
    """Meta-learner

    The meta-learner is responsible for sampling the trajectories/episodes 
    (before and after the one-step adaptation), compute the inner loss, compute 
    the updated parameters based on the inner-loss, and perform the meta-update.

    [1] Chelsea Finn, Pieter Abbeel, Sergey Levine, "Model-Agnostic 
        Meta-Learning for Fast Adaptation of Deep Networks", 2017 
        (https://arxiv.org/abs/1703.03400)
    [2] Richard Sutton, Andrew Barto, "Reinforcement learning: An introduction",
        2018 (http://incompleteideas.net/book/the-book-2nd.html)
    [3] John Schulman, Philipp Moritz, Sergey Levine, Michael Jordan, 
        Pieter Abbeel, "High-Dimensional Continuous Control Using Generalized 
        Advantage Estimation", 2016 (https://arxiv.org/abs/1506.02438)
    [4] John Schulman, Sergey Levine, Philipp Moritz, Michael I. Jordan, 
        Pieter Abbeel, "Trust Region Policy Optimization", 2015
        (https://arxiv.org/abs/1502.05477)
    """
    def __init__(self, net, device='cpu', beg_token=None, end_token = None, adaptive=False, samples=5, train_data_support=None, rev_emb_dict=None, first_order=False, fast_lr=0.001, meta_optimizer_lr=0.0001, dial_shown = False):
        self.net = net
        self.device = device
        self.beg_token = beg_token
        self.end_token = end_token
        # The training data from which the top-N samples (support set) are found.
        self.train_data_support = train_data_support
        self.rev_emb_dict = rev_emb_dict
        self.adaptive = adaptive
        self.samples = samples
        self.first_order = first_order
        self.fast_lr = fast_lr
        # self.meta_optimizer = optim.Adam(self.trainable_parameters(), lr=args.meta_learning_rate, amsgrad=False)
        self.meta_optimizer = optim.Adam(net.parameters(), lr=meta_optimizer_lr, eps=1e-3)
        self.dial_shown = dial_shown
        self.retriever = retriever.Retriever()

    def meta_update(self, loss):
        """
        Applies an outer loop update on the meta-parameters of the model.
        :param loss: The current crossentropy loss.
        """
        # optimizer.zero_grad() clears x.grad for every parameter x in the optimizer.
        # It’s important to call this before loss.backward(),
        # otherwise you’ll accumulate the gradients from multiple passes.
        self.meta_optimizer.zero_grad()
        # loss.backward() computes dloss/dx for every parameter x which has requires_grad=True.
        # These are accumulated into x.grad for every parameter x. In pseudo-code:
        # x.grad += dloss/dx
        loss.backward()
        # To conduct a gradient ascent to minimize the loss (which is to maximize the reward).
        # optimizer.step updates the value of x using the gradient x.grad.
        # For example, the SGD optimizer performs:
        # x += -lr * x.grad
        self.meta_optimizer.step()

    def get_inner_loop_parameter_dict(self, params):
        """
        Returns a dictionary with the parameters to use for inner loop updates.
        :param params: A dictionary of the network's parameters.
        :return: A dictionary of the parameters to use for the inner loop optimization process.
        """
        param_dict = dict()
        for name, param in params:
            if param.requires_grad:
                # When you get the parameters of your net, it does not clone the tensors.
                # So in your case, before and after contain the same tensors.
                # So when the optimizer update the weights in place, it updates both your lists.
                # You could call .clone() on each parameter so that a deep copy will be used to solve the problem.
                # param_dict[name] = param.to(device=self.device).clone()
                param_dict[name] = param.to(device=self.device)
        return param_dict

    def establish_support_set(self, train_data, N=0):
        # Find top-N in train_data_support;
        # get_top_N(train_data, train_data_support, N)
        # Support set is none. Use the training date per se as support set.
        batch = list()
        if N==0:
            batch.append(train_data)
        return batch

    def update_params(self, inner_loss, names_weights_copy, step_size=0.1, first_order=False):
        # NOTE: what is meaning of one step here? What is one step?
        # One step here means the loss of a batch of samples (a task) are computed.
        """Apply one step of gradient descent on the loss function `loss`, with
        step-size `step_size`, and returns the updated parameters of the neural
        network.
        """
        # nn.Module.zero_grad() Sets gradients of all model parameters to zero.
        # It’s important to call this before loss.backward(),
        # otherwise you’ll accumulate the gradients from multiple passes.
        self.net.zero_grad(names_weights_copy)
        # self.net.zero_grad()

        # create_graph (bool, optional) – If True, graph of the derivative will be constructed,
        # allowing to compute higher order derivative products. Defaults to False.
        # first_order is set as false and not first_order is true, so create_graph is set as true,
        # which means allowing to compute higher order derivative products.
        # self.parameters(): Returns an iterator over module parameters.
        # torch.autograd.grad(outputs, inputs, grad_outputs=None, retain_graph=None, create_graph=False, only_inputs=True, allow_unused=False):
        # outputs (sequence of Tensor) – outputs of the differentiated function.
        # inputs (sequence of Tensor) – Inputs w.r.t. which the gradient will be returned (and not accumulated into .grad).
        # In this case, the gradient of self.parameters() is inputs.
        # The autograd.grad function returns an object that match the inputs argument,
        # so we could get the the gradient of self.parameters() as returned value.
        # Here if we do not use the first_order configuration, we set it as true.
        grads = torch.autograd.grad(inner_loss, names_weights_copy.values(),
            create_graph=not first_order)
        # grads = torch.autograd.grad(inner_loss, self.net.parameters(),
        #                             create_graph=not first_order)
        # self.named_parameters() is consisted of an iterator over module parameters,
        # yielding both the name of the parameter as well as the parameter itself.
        # grads is the gradient of self.parameters().
        # Each module parameter is computed as parameter = parameter - step_size * grad.
        # When being saved in the OrderedDict of self.named_parameters(), it likes:
        # OrderedDict([('sigma', Parameter containing:
        # tensor([0.6931, 0.6931], requires_grad=True)), ('0.weight', Parameter containing:
        # tensor([[1., 1.],
        #         [1., 1.]], requires_grad=True)), ('0.bias', Parameter containing:
        # tensor([0., 0.], requires_grad=True)), ('1.weight', Parameter containing:
        # tensor([[1., 1.],
        #         [1., 1.]], requires_grad=True)), ('1.bias', Parameter containing:
        # tensor([0., 0.], requires_grad=True))])
        updated_names_weights_dict = dict()
        names_grads_wrt_params = dict(zip(names_weights_copy.keys(), grads))
        for key in names_grads_wrt_params.keys():
            print(str(key)+': ')
            print('names_weights_copy:')
            print(names_weights_copy[key])
            print('names_grads_wrt_params:')
            print(names_grads_wrt_params[key])
            updated_names_weights_dict[key] = names_weights_copy[key] - step_size * names_grads_wrt_params[key]
            print('updated_names_weights_dict:')
            print(updated_names_weights_dict[key])

        # for (name, param), grad in zip(self.net.named_parameters(), grads):
        #     updated_names_weights_dict[name] = param - step_size * grad
        #     print(str(name) + ': ')
        #     print('self.net.named_parameters:')
        #     print(param)
        #     print('grad:')
        #     print(grad)
        #     print('updated_names_weights_dict:')
        #     print(updated_names_weights_dict[name])

        return updated_names_weights_dict

    # The loss used to calculate theta' for each pseudo-task.
    # Compute the inner loss for the one-step gradient update.
    # The inner loss is REINFORCE with baseline [2].
    def inner_loss(self, task, weights=None):

        total_samples = 0
        skipped_samples = 0
        # todo: remember to change into real top-N.
        batch = self.establish_support_set(task)

        if weights is not None:
            self.net.insert_new_parameter(weights, True)
        # input_seq: the padded and embedded batch-sized input sequence.
        # input_batch: the token ID matrix of batch-sized input sequence. Each row is corresponding to one input sentence.
        # output_batch: the token ID matrix of batch-sized output sequences. Each row is corresponding to a list of several output sentences.
        input_seq, input_batch, output_batch = model.pack_batch_no_out(batch, self.net.emb, self.device)
        input_seq = input_seq.cuda()
        # Get (two-layer) hidden state of encoder of samples in batch.
        # enc = net.encode(input_seq)
        context, enc = self.net.encode_context(input_seq)

        net_policies = []
        net_actions = []
        net_advantages = []
        # Transform ID to embedding.
        beg_embedding = self.net.emb(self.beg_token)
        beg_embedding = beg_embedding.cuda()

        for idx, inp_idx in enumerate(input_batch):
            # # Test whether the input sequence is correctly transformed into indices.
            # input_tokens = [rev_emb_dict[temp_idx] for temp_idx in inp_idx]
            # print (input_tokens)
            # Get IDs of reference sequences' tokens corresponding to idx-th input sequence in batch.
            qa_info = output_batch[idx]
            print("%s is training..." % (qa_info['qid']))
            # print (qa_info['qid'])
            # # Get the (two-layer) hidden state of encoder of idx-th input sequence in batch.
            item_enc = self.net.get_encoded_item(enc, idx)
            # # 'r_argmax' is the list of out_logits list and 'actions' is the list of output tokens.
            # # The output tokens are generated greedily by using chain_argmax (using last setp's output token as current input token).
            r_argmax, actions = self.net.decode_chain_argmax(item_enc, beg_embedding, data.MAX_TOKENS, context[idx],
                                                             stop_at_token=self.end_token)
            # Show what the output action sequence is.
            action_tokens = []
            for temp_idx in actions:
                if temp_idx in self.rev_emb_dict and self.rev_emb_dict.get(temp_idx) != '#END':
                    action_tokens.append(str(self.rev_emb_dict.get(temp_idx)).upper())
            # Get the highest BLEU score as baseline used in self-critic.
            # If the last parameter is false, it means that the 0-1 reward is used to calculate the accuracy.
            # Otherwise the adaptive reward is used.
            # todo: remember to change into utils.calc_True_Reward;
            # argmax_reward = utils.calc_True_Reward(action_tokens, qa_info, self.adaptive)
            argmax_reward = random.random()
            # true_reward_argmax.append(argmax_reward)

            # # In this case, the BLEU score is so high that it is not needed to train such case with RL.
            # if not args.disable_skip and argmax_reward > 0.99:
            #     skipped_samples += 1
            #     continue

            # # In one epoch, when model is optimized for the first time, the optimized result is displayed here.
            # # After that, all samples in this epoch don't display anymore.
            # if not self.dial_shown:
            #     # data.decode_words transform IDs to tokens.
            #     log.info("Input: %s", utils.untokenize(data.decode_words(inp_idx, self.rev_emb_dict)))
            #     orig_response = qa_info['orig_response']
            #     log.info("orig_response: %s", orig_response)
            #     log.info("Argmax: %s, reward=%.4f", utils.untokenize(data.decode_words(actions, self.rev_emb_dict)),
            #              argmax_reward)

            action_memory = list()
            for _ in range(self.samples):
                # 'r_sample' is the list of out_logits list and 'actions' is the list of output tokens.
                # The output tokens are sampled following probabilitis by using chain_sampling.
                r_sample, actions = self.net.decode_chain_sampling(item_enc, beg_embedding, data.MAX_TOKENS,
                                                                   context[idx], stop_at_token=self.end_token)
                total_samples += 1

                # Omit duplicate action sequence to decrease the computing time and to avoid the case that
                # the probability of such kind of duplicate action sequences would be increased redundantly and abnormally.
                duplicate_flag = False
                if len(action_memory) > 0:
                    for temp_list in action_memory:
                        if utils.duplicate(temp_list, actions):
                            duplicate_flag = True
                            break
                if not duplicate_flag:
                    action_memory.append(actions)
                else:
                    skipped_samples += 1
                    continue
                # Show what the output action sequence is.
                action_tokens = []
                for temp_idx in actions:
                    if temp_idx in self.rev_emb_dict and self.rev_emb_dict.get(temp_idx) != '#END':
                        action_tokens.append(str(self.rev_emb_dict.get(temp_idx)).upper())

                # If the last parameter is false, it means that the 0-1 reward is used to calculate the accuracy.
                # Otherwise the adaptive reward is used.
                # todo: remember to change into utils.calc_True_Reward;
                sample_reward = random.random()
                # sample_reward = utils.calc_True_Reward(action_tokens, qa_info, self.adaptive)
                # true_reward_sample.append(sample_reward)


                # if not self.dial_shown:
                #     log.info("Sample: %s, reward=%.4f", utils.untokenize(data.decode_words(actions, self.rev_emb_dict)),
                #              sample_reward)

                net_policies.append(r_sample)
                net_actions.extend(actions)
                # Regard argmax_bleu calculated from decode_chain_argmax as baseline used in self-critic.
                # Each token has same reward as 'sample_bleu - argmax_bleu'.
                # [x] * y: stretch 'x' to [1*y] list in which each element is 'x'.

                # # If the argmax_reward is 1.0, then whatever the sample_reward is,
                # # the probability of actions that get reward = 1.0 could not be further updated.
                # # The GAMMA is used to adjust this scenario.
                # if argmax_reward == 1.0:
                #     net_advantages.extend([sample_reward - argmax_reward + GAMMA] * len(actions))
                # else:
                #     net_advantages.extend([sample_reward - argmax_reward] * len(actions))

                net_advantages.extend([sample_reward - argmax_reward] * len(actions))

            # self.dial_shown = True
            # print("Epoch %d, Batch %d, Sample %d: %s is trained!" % (epoch, batch_count, idx, qa_info['qid']))

        if not net_policies:
            log.info("The net_policies is empty!")
            # TODO the format of 0.0 should be the same as loss_v.
            return 0.0, total_samples, skipped_samples

        # Data for decode_chain_sampling samples and the number of such samples is the same as args.samples parameter.
        # Logits of all output tokens whose size is N * output vocab size; N is the number of output tokens of decode_chain_sampling samples.
        policies_v = torch.cat(net_policies)
        policies_v = policies_v.cuda()
        # Indices of all output tokens whose size is 1 * N;
        actions_t = torch.LongTensor(net_actions).to(self.device)
        actions_t = actions_t.cuda()
        # All output tokens reward whose size is 1 * N;
        adv_v = torch.FloatTensor(net_advantages).to(self.device)
        adv_v = adv_v.cuda()
        # Compute log(softmax(logits)) of all output tokens in size of N * output vocab size;
        log_prob_v = F.log_softmax(policies_v, dim=1)
        log_prob_v = log_prob_v.cuda()
        # Q_1 = Q_2 =...= Q_n = BLEU(OUT,REF);
        # ▽J = Σ_n[Q▽logp(T)] = ▽Σ_n[Q*logp(T)] = ▽[Q_1*logp(T_1)+Q_2*logp(T_2)+...+Q_n*logp(T_n)];
        # log_prob_v[range(len(net_actions)), actions_t]: for each output, get the output token's log(softmax(logits)).
        # adv_v * log_prob_v[range(len(net_actions)), actions_t]:
        # get Q * logp(T) for all tokens of all decode_chain_sampling samples in size of 1 * N;
        log_prob_actions_v = adv_v * log_prob_v[range(len(net_actions)), actions_t]
        log_prob_actions_v = log_prob_actions_v.cuda()
        # For the optimizer is Adam (Adaptive Moment Estimation) which is a optimizer used for gradient descent.
        # Therefore, to maximize ▽J (log_prob_actions_v) is to minimize -▽J.
        # .mean() is to calculate Monte Carlo sampling.
        loss_policy_v = -log_prob_actions_v.mean()
        loss_policy_v = loss_policy_v.cuda()

        loss_v = loss_policy_v
        return loss_v, total_samples, skipped_samples

    def sample(self, tasks, first_order=False):
        """Sample trajectories (before and after the update of the parameters)
        for all the tasks `tasks`.
        Here number of tasks is 8.
        """
        task_losses = []
        total_samples = 0
        skipped_samples = 0
        self.net.zero_grad()
        # To get copied weights of the model for inner training.
        # initial_names_weights_copy = self.get_inner_loop_parameter_dict(self.net.named_parameters())
        for task in tasks:

            names_weights_copy = self.get_inner_loop_parameter_dict(self.net.named_parameters())
            # names_weights_copy = initial_names_weights_copy
            inner_loss, inner_total_samples, inner_skipped_samples = self.inner_loss(task, weights=names_weights_copy)

            total_samples += inner_total_samples
            skipped_samples += inner_skipped_samples

            # Get the new parameters after a one-step gradient update
            # Each module parameter is computed as parameter = parameter - step_size * grad.
            # When being saved in the OrderedDict of self.named_parameters(), it likes:
            # OrderedDict([('sigma', Parameter containing:
            # tensor([0.6931, 0.6931], requires_grad=True)), ('0.weight', Parameter containing:
            # tensor([[1., 1.],
            #         [1., 1.]], requires_grad=True)), ('0.bias', Parameter containing:
            # tensor([0., 0.], requires_grad=True)), ('1.weight', Parameter containing:
            # tensor([[1., 1.],
            #         [1., 1.]], requires_grad=True)), ('1.bias', Parameter containing:
            # tensor([0., 0.], requires_grad=True))])
            names_weights_copy = self.update_params(inner_loss, names_weights_copy=names_weights_copy, step_size=self.fast_lr,
                                                    first_order=self.first_order)

            inner_loss, inner_total_samples, inner_skipped_samples = self.inner_loss(task, weights=names_weights_copy)
            total_samples += inner_total_samples
            skipped_samples += inner_skipped_samples
            names_weights_copy = self.update_params(inner_loss, names_weights_copy=names_weights_copy, step_size=self.fast_lr,
                                                        first_order=self.first_order)

            meta_loss, outer_total_samples, outer_skipped_samples = self.inner_loss(task, weights=names_weights_copy)
            task_losses.append(meta_loss)
        meta_losses = torch.mean(torch.stack(task_losses))
        return meta_losses, total_samples, skipped_samples

    def kl_divergence(self, episodes, old_pis=None):
        kls = []
        if old_pis is None:
            old_pis = [None] * len(episodes)

        for (train_episodes, valid_episodes), old_pi in zip(episodes, old_pis):
            params = self.adapt(train_episodes)
            pi = self.policy(valid_episodes.observations, params=params)

            if old_pi is None:
                old_pi = detach_distribution(pi)

            mask = valid_episodes.mask
            if valid_episodes.actions.dim() > 2:
                mask = mask.unsqueeze(2)
            kl = weighted_mean(kl_divergence(pi, old_pi), dim=0, weights=mask)
            kls.append(kl)

        return torch.mean(torch.stack(kls, dim=0))

    def hessian_vector_product(self, episodes, damping=1e-2):
        """Hessian-vector product, based on the Perlmutter method."""
        def _product(vector):
            kl = self.kl_divergence(episodes)
            grads = torch.autograd.grad(kl, self.policy.parameters(),
                create_graph=True)
            flat_grad_kl = parameters_to_vector(grads)

            grad_kl_v = torch.dot(flat_grad_kl, vector)
            grad2s = torch.autograd.grad(grad_kl_v, self.policy.parameters())
            flat_grad2_kl = parameters_to_vector(grad2s)

            return flat_grad2_kl + damping * vector
        return _product

    def surrogate_loss(self, episodes, old_pis=None):
        losses, kls, pis = [], [], []
        if old_pis is None:
            # It is: [None, None, None,..., None, None, None, None, None], a list of 40 'None'.
            old_pis = [None] * len(episodes)

        for (train_episodes, valid_episodes), old_pi in zip(episodes, old_pis):
            # Self.policy.named_parameters() are initialized when the model is established.
            # The updated parameters are saved in params but the value of named_parameters() is not changed.
            # By using saved train_episodes in one task, the adapted parameters which are used in this task is generated.
            params = self.adapt(train_episodes)
            # with torch.set_grad_enabled (true or false): to set the operations in the block be able to compute gradient.
            # The torch.set_grad_enabled line of code makes sure to clear the intermediate values for evaluation,
            # which are needed to backpropagate during training, thus saving memory.
            # It’s comparable to the with torch.no_grad() statement but takes a bool value.
            # All new operations in the torch.set_grad_enabled(False) block won’t require gradients.
            # However, the model parameters will still require gradients.
            with torch.set_grad_enabled(old_pi is None):
                # episodes.observations: [2,4,3] -> pi: [2,4,5] (2:steps, 4:samples, 5:action_size)
                # Using policy's mlp to compute actions, i.e., pi, from observations.
                pi = self.policy(valid_episodes.observations, params=params)
                # detach_distribution(pi) = Normal(loc=pi.loc.detach(), scale=pi.scale.detach())
                # NOTE: Why detach_distribution(pi)?
                pis.append(detach_distribution(pi))

                if old_pi is None:
                    old_pi = detach_distribution(pi)
                # Using learned weight to calculate returns (cumulative rewards) in baseline.
                values = self.baseline(valid_episodes)
                # Get advantages of valid_episodes.
                advantages = valid_episodes.gae(values, tau=self.tau)
                advantages = weighted_normalize(advantages,
                    weights=valid_episodes.mask)
                # NOTE: WHY?
                # log_prob here is used to measure the variance of actions computed by `Normal` distribution output of NormalMLPPolicy。
                # Suppose an output action is a vector of elements,
                # then pi.log_prob(actions) is to compute the probability of each element in the action vector.
                # The probability of the action is computed as the product of probabilities of all elements in the action vector.
                # So the sum of elements in pi.log_prob(actions) is the log_prob of the whole action.
                log_ratio = (pi.log_prob(valid_episodes.actions)
                    - old_pi.log_prob(valid_episodes.actions))
                # [2, 4, 5] -> [2, 4]
                if log_ratio.dim() > 2:
                    log_ratio = torch.sum(log_ratio, dim=2)
                ratio = torch.exp(log_ratio)

                # For example: tensor(-0.3125, grad_fn=<NegBackward>);
                loss = -weighted_mean(ratio * advantages, dim=0,
                    weights=valid_episodes.mask)
                losses.append(loss)

                mask = valid_episodes.mask
                if valid_episodes.actions.dim() > 2:
                    # From:
                    # mask: torch.Size([2, 4])
                    # tensor([[1., 1., 1., 1.],
                    #         [0., 0., 0., 0.]])
                    # To:
                    # unsqueeze(2) mask: torch.Size([2, 4, 1])
                    # tensor([[[1.],
                    #          [1.],
                    #          [1.],
                    #          [1.]],
                    #
                    #         [[0.],
                    #          [0.],
                    #          [0.],
                    #          [0.]]])
                    mask = mask.unsqueeze(2)
                # For instance: kl: tensor(0., grad_fn=<MeanBackward1>)
                kl = weighted_mean(kl_divergence(pi, old_pi), dim=0,
                    weights=mask)
                kls.append(kl)

        # torch.stack(tensors, dim=0, out=None) → Tensor:
        # Concatenates sequence of tensors along a new dimension into a list.
        # All tensors need to be of the same size.
        # Return the average of losses and kls of the batch of tasks,
        # and the pis of the batch of tasks as well.
        return (torch.mean(torch.stack(losses, dim=0)),
                torch.mean(torch.stack(kls, dim=0)), pis)

    def step(self, episodes, max_kl=1e-3, cg_iters=10, cg_damping=1e-2,
             ls_max_steps=10, ls_backtrack_ratio=0.5):
        """Meta-optimization step (ie. update of the initial parameters), based
        on Trust Region Policy Optimization (TRPO, [4]).
        """
        # Get the average of losses of all episodes of all episodes in a batch of 40 tasks.
        # Get the detached distributions, old_pis for all episodes in a batch of 40 tasks.
        old_loss, _, old_pis = self.surrogate_loss(episodes)
        # torch.autograd.grad(outputs, inputs, grad_outputs=None,
        # retain_graph=None, create_graph=False, only_inputs=True, allow_unused=False):
        # Computes and returns the sum of gradients of outputs w.r.t. the inputs.
        # outputs (sequence of Tensor) – outputs of the differentiated function.
        # inputs (sequence of Tensor) – Inputs w.r.t. which the gradient will be returned
        # (and not accumulated into .grad).
        grads = torch.autograd.grad(old_loss, self.policy.parameters())
        # Convert parameters to one vector, concat each parameter (vector) into one vector.
        grads = parameters_to_vector(grads)

        # Compute the step direction with Conjugate Gradient
        hessian_vector_product = self.hessian_vector_product(episodes,
            damping=cg_damping)
        # hessian_vector_product here is the pointer of the function _product defined in hessian_vector_product
        # and will transferred into conjugate_gradient.
        stepdir = conjugate_gradient(hessian_vector_product, grads,
            cg_iters=cg_iters)

        # Compute the Lagrange multiplier
        shs = 0.5 * torch.dot(stepdir, hessian_vector_product(stepdir))
        lagrange_multiplier = torch.sqrt(shs / max_kl)

        step = stepdir / lagrange_multiplier

        # Save the old parameters
        old_params = parameters_to_vector(self.policy.parameters())

        # Line search
        step_size = 1.0
        for _ in range(ls_max_steps):
            # Convert value of the vector into the value of parameters.
            vector_to_parameters(old_params - step_size * step,
                                 self.policy.parameters())
            loss, kl, _ = self.surrogate_loss(episodes, old_pis=old_pis)
            # If new loss is less then old loss, which means having found better parameters.
            improve = loss - old_loss
            if (improve.item() < 0.0) and (kl.item() < max_kl):
                break
            step_size *= ls_backtrack_ratio
        else:
            vector_to_parameters(old_params, self.policy.parameters())
