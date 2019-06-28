import os
import gym
import argparse
import numpy as np
from collections import deque

import torch
import torch.optim as optim

from utils import *
from model import Actor
from tensorboardX import SummaryWriter

parser = argparse.ArgumentParser()
parser.add_argument('--env_name', type=str, default="Pendulum-v0")
parser.add_argument('--load_model', type=str, default=None)
parser.add_argument('--save_path', default='./save_model/', help='')
parser.add_argument('--render', action="store_true", default=False)
parser.add_argument('--gamma', type=float, default=0.99)
parser.add_argument('--hidden_size', type=int, default=64)
parser.add_argument('--max_kl', type=float, default=1e-2)
parser.add_argument('--max_iter_num', type=int, default=500)
parser.add_argument('--total_sample_size', type=int, default=2048)
parser.add_argument('--log_interval', type=int, default=5)
parser.add_argument('--goal_score', type=int, default=-300)
parser.add_argument('--logdir', type=str, default='./logs',
                    help='tensorboardx logs directory')
args = parser.parse_args()

def train_model(actor, trajectories, state_size, action_size):
    trajectories = np.array(trajectories)
    states = np.vstack(trajectories[:, 0])
    actions = list(trajectories[:, 1])
    rewards = list(trajectories[:, 2])
    masks = list(trajectories[:, 3])

    actions = torch.Tensor(actions).squeeze(1)
    rewards = torch.Tensor(rewards).squeeze(1)
    masks = torch.Tensor(masks)

    # ----------------------------
    # step 1: get returns
    returns = get_returns(rewards, masks, args.gamma)

    # ----------------------------
    # step 2: get gradient of actor loss and search direction through conjugate gradient method
    mu, std = actor(torch.Tensor(states))
    old_policy = get_log_prob(actions, mu, std)
    actor_loss = surrogate_loss(actor, returns, states, old_policy.detach(), actions)
    
    actor_loss_grad = torch.autograd.grad(actor_loss, actor.parameters())
    actor_loss_grad = flat_grad(actor_loss_grad)
    
    search_dir = conjugate_gradient(actor, states, actor_loss_grad.data, nsteps=10)
    
    actor_loss = actor_loss.data.numpy()
    
    # ----------------------------
    # step 3: get step-size alpha and maximal step
    sHs = 0.5 * (search_dir * hessian_vector_product(actor, states, search_dir)
                 ).sum(0, keepdim=True)
    step_size = torch.sqrt(2 * args.max_kl / sHs)[0]
    maximal_step = step_size * search_dir

    # ----------------------------    
    # step 4: update actor and perform backtracking line search for n iteration
    params = flat_params(actor)
    
    old_actor = Actor(state_size, action_size, args)
    update_model(old_actor, params)
    
    # 구했던 maximal step만큼 parameter space에서 움직였을 때 예상되는 performance 변화
    expected_improve = (actor_loss_grad * maximal_step).sum(0, keepdim=True)
    expected_improve = expected_improve.data.numpy()

    # Backtracking line search
    # see cvx 464p https://web.stanford.edu/~boyd/cvxbook/bv_cvxbook.pdf
    # additionally, https://en.wikipedia.org/wiki/Backtracking_line_search
    flag = False
    alpha = 0.5
    beta = 0.5
    t = 1.0

    for i in range(10):
        new_params = params + t * maximal_step
        update_model(actor, new_params)
        
        new_actor_loss = surrogate_loss(actor, returns, states, old_policy.detach(), actions)
        new_actor_loss = new_actor_loss.data.numpy()

        loss_improve = new_actor_loss - actor_loss
        expected_improve *= t
        improve_condition = loss_improve / expected_improve

        kl = kl_divergence(new_actor=actor, old_actor=old_actor, states=states)
        kl = kl.mean()

        # print('kl: {:.4f} | loss_improve: {:.4f} | expected_improve: {:.4f} '
        #       '| improve_condition: {:.4f} | number of line search: {}'
        #       .format(kl.data.numpy(), loss_improve, expected_improve[0], improve_condition[0], i))

        # kl-divergence와 expected_new_actor_loss_grad와 함께 trust region 안에 있는지 밖에 있는지를 판단
        # trust region 안에 있으면 loop 탈출
        # max_kl = 0.01
        if kl < args.max_kl and improve_condition > alpha:
            flag = True
            break

        # trust region 밖에 있으면 maximal_step을 반만큼 쪼개서 다시 실시
        t *= beta

    if not flag:
        params = flat_params(old_actor)
        update_model(actor, params)
        print('policy update does not impove the surrogate')


def main():
    env = gym.make(args.env_name)
    env.seed(500)
    torch.manual_seed(500)

    state_size = env.observation_space.shape[0]
    action_size = env.action_space.shape[0]
    print('state size:', state_size)
    print('action size:', action_size)
    
    actor = Actor(state_size, action_size, args)

    # writer = SummaryWriter(args.logdir)

    recent_rewards = deque(maxlen=100)
    episodes = 0

    for iter in range(args.max_iter_num):
        trajectories = deque()
        steps = 0

        while steps < args.total_sample_size: 
            done = False
            score = 0
            episodes += 1

            state = env.reset()
            state = np.reshape(state, [1, state_size])

            while not done:
                if args.render:
                    env.render()

                steps += 1

                mu, std = actor(torch.Tensor(state))
                action = get_action(mu, std)

                next_state, reward, done, _ = env.step(action)
                
                mask = 0 if done else 1

                trajectories.append((state, action, reward, mask))

                next_state = np.reshape(next_state, [1, state_size])
                state = next_state
                score += reward

                if done:
                    recent_rewards.append(score)

        if iter % args.log_interval == 0:
            print('{} iter | {} episode | score_avg: {:.2f}'.format(iter, episodes, np.mean(recent_rewards)))
            # writer.add_scalar('log/score', float(score), iter)
        
        actor.train()
        train_model(actor, trajectories, state_size, action_size)

        if np.mean(recent_rewards) > args.goal_score:
            if not os.path.isdir(args.save_path):
                os.makedirs(args.save_path)
            
            ckpt_path = args.save_path + 'model.pth'
            torch.save(actor.state_dict(), ckpt_path)
            print('Recent rewards exceed -300. So end')
            break  

if __name__ == '__main__':
    main()