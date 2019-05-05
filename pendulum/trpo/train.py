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
parser.add_argument('--goal_score', type=int, default=-200)
parser.add_argument('--logdir', type=str, default='./logs',
                    help='tensorboardx logs directory')
args = parser.parse_args()
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

def train_model(actor, memory, state_size, action_size, args):
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
    mu, std = actor(torch.Tensor(states))
    old_policy = log_prob_density(torch.Tensor(actions), mu, std)
    loss = surrogate_loss(actor, returns, states, old_policy.detach(), actions)
    
    loss_grad = torch.autograd.grad(loss, actor.parameters())
    loss_grad = flat_grad(loss_grad)
    loss = loss.data.numpy()
    
    step_dir = conjugate_gradient(actor, states, loss_grad.data, nsteps=10)
    
    # ----------------------------
    # step 3: get step-size alpha and maximal step
    sHs = 0.5 * (step_dir * hessian_vector_product(actor, states, step_dir)
                 ).sum(0, keepdim=True)
    step_size = torch.sqrt(2 * args.max_kl / sHs)[0]
    maximal_step = step_size * step_dir

    # ----------------------------    
    # step 4: perform backtracking line search for n iteration
    old_actor = Actor(state_size, action_size, args)
    params = flat_params(actor)
    update_model(old_actor, params)
    
    # 구했던 maximal step만큼 parameter space에서 움직였을 때 예상되는 performance 변화
    expected_improve = (loss_grad * maximal_step).sum(0, keepdim=True)
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
        
        new_loss = surrogate_loss(actor, returns, states, old_policy.detach(), actions)
        new_loss = new_loss.data.numpy()

        loss_improve = new_loss - loss
        expected_improve *= t
        improve_condition = loss_improve / expected_improve

        kl = kl_divergence(old_actor=old_actor, new_actor=actor, states=states)
        kl = kl.mean()

        # print('kl: {:.4f} | loss_improve: {:.4f} | expected_improve: {:.4f} '
        #       '| improve_condition: {:.4f} | number of line search: {}'
        #       .format(kl.data.numpy(), loss_improve, expected_improve[0], improve_condition[0], i))

        # kl-divergence와 expected_new_loss_grad와 함께 trust region 안에 있는지 밖에 있는지를 판단
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
        train_model(actor, memory, state_size, action_size, args)

        if np.mean(recent_rewards) > args.goal_score:
            ckpt_path = args.save_path + 'model.pth'
            torch.save(actor.state_dict(), ckpt_path)
            print('Recent rewards exceed -200. So end')
            break  

if __name__ == '__main__':
    main()