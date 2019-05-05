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
parser.add_argument('--max_iter_num', type=int, default=1000)
parser.add_argument('--total_sample_size', type=int, default=2048)
parser.add_argument('--log_interval', type=int, default=5)
parser.add_argument('--goal_score', type=int, default=-200)
parser.add_argument('--logdir', type=str, default='./logs',
                    help='tensorboardx logs directory')
args = parser.parse_args()
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

def train_model(actor, memory, args):
    memory = np.array(memory)
    states = np.vstack(memory[:, 0])
    actions = list(memory[:, 1])
    rewards = list(memory[:, 2])
    masks = list(memory[:, 3])

    # ----------------------------
    # step 1: get returns
    returns = get_returns(rewards, masks, args.gamma)

    # ----------------------------
    # step 2: get gradient of loss and hessian of kl and step direction
    loss = get_loss(actor, returns, states, actions)
    loss_grad = torch.autograd.grad(loss, actor.parameters())
    loss_grad = flat_grad(loss_grad)

    step_dir = conjugate_gradient(actor, states, loss_grad.data, nsteps=10)
    
    # ----------------------------
    # step 3: update actor
    params = flat_params(actor)
    new_params = params + 0.5 * step_dir
    update_model(actor, new_params)
    

def main():
    env = gym.make(args.env_name)
    env.seed(500)
    torch.manual_seed(500)

    state_size = env.observation_space.shape[0]
    action_size = env.action_space.shape[0]
    print('state size:', state_size)
    print('action size:', action_size)
    
    actor = Actor(state_size, action_size, args).to(device)
    # writer = SummaryWriter(args.logdir)

    if not os.path.isdir(args.save_path):
        os.makedirs(args.save_path)

    recent_rewards = deque(maxlen=100)
    episodes = 0

    for iter in range(args.max_iter_num):
        memory = deque()
        steps = 0

        while steps < args.total_sample_size:
            score = 0
            episodes += 1

            state = env.reset()

            for _ in range(200):
                if args.render:
                    env.render()

                steps += 1

                mu, std = actor(torch.Tensor(state).unsqueeze(0))
                action = get_action(mu, std)[0]
                next_state, reward, done, _ = env.step(action)

                if done:
                    mask = 0
                else:
                    mask = 1

                memory.append([state, action, reward, mask])

                state = next_state
                score += reward

                if done:
                    recent_rewards.append(score)

        if iter % args.log_interval == 0:
            print('{} iter | {} episode | score_avg: {:.2f}'.format(iter, episodes, np.mean(recent_rewards)))
            # writer.add_scalar('log/score', float(score), iter)
        
        actor.train()
        train_model(actor, memory, args)

        if np.mean(recent_rewards) > args.goal_score:
            ckpt_path = args.save_path + 'model.pth'
            torch.save(actor.state_dict(), ckpt_path)
            print('Recent rewards exceed -200. So end')
            break  

if __name__ == '__main__':
    main()